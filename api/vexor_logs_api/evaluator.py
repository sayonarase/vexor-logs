"""Background daemon: evaluate log alert rules every 30 s.

Polls VictoriaLogs for each enabled rule, counts matches in the configured
window, and:
  * POSTs to vexor-api's /v1/notifications/dispatch when threshold is exceeded
  * if the rule has ``host_binding`` set, submits a passive service check
    result to Naemon so the failure shows up in the monitoring console.

State (last_fired/last_count) is persisted in the shared database.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import _client
from .models import LogAlertRule
from .naemon_passive import slugify_rule_name, service_name, submit_passive_result

log = logging.getLogger("vexor.logs.evaluator")

POLL_INTERVAL = int(os.environ.get("VEXOR_LOG_ALERTS_INTERVAL", "30"))
NOTIFY_URL    = os.environ.get(
    "VEXOR_NOTIFY_URL", "http://127.0.0.1:8000/v1/notifications/dispatch"
)

_SEV_TO_RC = {"info": 0, "ok": 0, "warning": 1, "critical": 2, "unknown": 3}


async def _evaluate_once(session_factory) -> None:
    async with session_factory() as db:
        rules = (await db.execute(
            select(LogAlertRule).where(LogAlertRule.enabled.is_(True))
        )).scalars().all()
        for r in rules:
            try:
                start = (datetime.now(timezone.utc) - timedelta(seconds=r.window_sec)).isoformat()
                qlimit = max(r.threshold * 10, 1000)
                def _do_query():
                    return _client.query(r.query, limit=qlimit, start=start)
                rows = await asyncio.to_thread(_do_query)
                count = len(rows)
                r.last_count = count
                fired = count >= r.threshold
                import os as _os
                COOLDOWN = int(_os.environ.get("VEXOR_LOG_ALERT_COOLDOWN_SEC", "600"))
                may_dispatch = False
                if fired:
                    now = datetime.now(timezone.utc)
                    lf = r.last_fired
                    if lf is None:
                        may_dispatch = True
                    else:
                        if lf.tzinfo is None:
                            lf = lf.replace(tzinfo=timezone.utc)
                        if (now - lf).total_seconds() >= COOLDOWN:
                            may_dispatch = True
                    if may_dispatch:
                        r.last_fired = now
                if may_dispatch:
                    await asyncio.to_thread(_dispatch, r, count, rows)
                # Always update naemon state when a binding exists so the
                # service moves back to OK when matches drop below threshold.
                host = getattr(r, "host_binding", None)
                if host:
                    svc = service_name(slugify_rule_name(r.name))
                    if fired:
                        rc = _SEV_TO_RC.get((r.severity or "warning").lower(), 1)
                        output = (f"{r.severity.upper()}: {count} matches in last "
                                  f"{r.window_sec}s (threshold {r.threshold})")
                    else:
                        rc = 0
                        output = f"OK: {count} matches in last {r.window_sec}s"
                    submit_passive_result(host, svc, rc, output)
                await db.commit()
            except Exception as e:
                log.exception("rule %s failed: %s", r.name, e)
                await db.rollback()


def _dispatch(rule: LogAlertRule, count: int, sample_rows: list) -> None:
    payload = {
        "source":   "vexor-logs",
        "rule":     rule.name,
        "severity": rule.severity,
        "summary":  f"[{rule.severity}] log rule '{rule.name}' matched {count} times",
        "query":    rule.query,
        "count":    count,
        "to":       rule.notify_to,
        "host":     getattr(rule, "host_binding", None),
        "sample":   sample_rows[:3],
    }
    try:
        req = urllib.request.Request(
            NOTIFY_URL, method="POST",
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
    except Exception as e:
        log.warning("notify dispatch failed: %s", e)


async def _amain() -> None:
    from app.database import async_session  # type: ignore

    log.info("vexor log alert evaluator starting (interval=%ss)", POLL_INTERVAL)
    while True:
        try:
            await _evaluate_once(async_session)
        except Exception as e:
            log.exception("evaluator iteration failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_amain())


if __name__ == "__main__":
    main()

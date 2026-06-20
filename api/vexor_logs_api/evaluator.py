"""Background daemon: evaluate log alert rules every 30 s.

Polls VictoriaLogs for each enabled rule, counts matches in the configured
window, and:
  * POSTs to vexor-api's /v1/notifications/dispatch when the rule fires
  * if the rule has ``host_binding`` set, submits a passive service check
    result to Naemon so the failure shows up in the monitoring console (and
    therefore in SLA reports, BSM and notifications).

Two evaluation modes (column ``mode``):
  * ``match``   - fire when the match count crosses warn/crit thresholds
                  (classic log-content alert, e.g. SSH brute force).
  * ``absence`` - fire when too FEW logs arrive in the window (dead-man /
                  "logs stopped coming in"). Used for per-host log-freshness
                  checks; goes CRITICAL when a host stops shipping logs.

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
from .models import LogAlertRule, LogAlertEvent
from .naemon_passive import (
    slugify_rule_name, service_name, submit_passive_result,
    host_exists, _validate_host_name, InvalidHostName,
)

log = logging.getLogger("vexor.logs.evaluator")

POLL_INTERVAL = int(os.environ.get("VEXOR_LOG_ALERTS_INTERVAL", "30"))
NOTIFY_URL    = os.environ.get(
    "VEXOR_NOTIFY_URL", "http://127.0.0.1:8000/v1/notifications/dispatch"
)

_SEV_TO_RC = {"info": 0, "ok": 0, "warning": 1, "critical": 2, "unknown": 3}


def evaluate_rule(mode: str, count: int, window_sec: int,
                  threshold: int, warn_threshold, crit_threshold,
                  severity: str) -> tuple[bool, int, str]:
    """Pure decision function. Returns (fired, naemon_rc, plugin_output).

    Shared with the unit tests so the alerting semantics are pinned.
    """
    mode = (mode or "match").lower()
    sev = (severity or "warning").lower()
    if mode == "absence":
        need = threshold if threshold and threshold > 0 else 1
        if count >= need:
            return (False, 0,
                    f"OK: {count} log line(s) received in last {window_sec}s "
                    f"(expected >= {need})")
        rc = _SEV_TO_RC.get(sev, 2)  # dead-man defaults to CRITICAL
        return (True, rc,
                f"{sev.upper()}: only {count} log line(s) in last {window_sec}s "
                f"(expected >= {need}) - source may be down")
    # --- match mode ---
    if crit_threshold is not None and count >= crit_threshold:
        return (True, 2,
                f"CRITICAL: {count} match(es) in last {window_sec}s "
                f"(>= {crit_threshold})")
    if warn_threshold is not None and count >= warn_threshold:
        return (True, 1,
                f"WARNING: {count} match(es) in last {window_sec}s "
                f"(>= {warn_threshold})")
    if warn_threshold is None and crit_threshold is None and count >= threshold:
        rc = _SEV_TO_RC.get(sev, 1)
        return (True, rc,
                f"{sev.upper()}: {count} match(es) in last {window_sec}s "
                f"(>= {threshold})")
    return (False, 0, f"OK: {count} match(es) in last {window_sec}s")


async def _evaluate_once(session_factory) -> None:
    async with session_factory() as db:
        rules = (await db.execute(
            select(LogAlertRule).where(LogAlertRule.enabled.is_(True))
        )).scalars().all()
        for r in rules:
            try:
                start = (datetime.now(timezone.utc) - timedelta(seconds=r.window_sec)).isoformat()
                mode = (getattr(r, "mode", None) or "match").lower()
                warn = getattr(r, "warn_threshold", None)
                crit = getattr(r, "crit_threshold", None)
                # For absence we only need to know whether the threshold is
                # reached, so a small limit is enough; for match we want to be
                # able to count up to the highest threshold of interest.
                hi = max([t for t in (r.threshold, warn, crit) if t] or [1])
                qlimit = max(hi * 10, 1000)

                def _do_query():
                    return _client.query(r.query, limit=qlimit, start=start)
                rows = await asyncio.to_thread(_do_query)
                count = len(rows)
                r.last_count = count

                fired, rc, output = evaluate_rule(
                    mode, count, r.window_sec, r.threshold, warn, crit, r.severity)

                prev_state = r.last_state
                if prev_state != rc:
                    db.add(LogAlertEvent(
                        rule_id=r.id, rule_name=r.name,
                        host=getattr(r, "host_binding", None),
                        mode=mode, severity=r.severity,
                        state=rc, prev_state=prev_state,
                        count=count, output=output,
                    ))
                    r.last_state = rc

                COOLDOWN = int(os.environ.get("VEXOR_LOG_ALERT_COOLDOWN_SEC", "600"))
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
                    await asyncio.to_thread(_dispatch, r, count, rows, mode, output)

                # Always update naemon state when a binding exists so the
                # service moves back to OK once the condition clears.
                host = getattr(r, "host_binding", None)
                if host:
                    # Only submit for a valid, known Naemon host. A stale or
                    # garbage host_binding would otherwise make naemon reject the
                    # external command every cycle and spam naemon.log (log-16).
                    try:
                        valid_host = _validate_host_name(host)
                        known = host_exists(valid_host)
                    except InvalidHostName:
                        valid_host, known = None, False
                    if not known:
                        log.debug("log rule %r: host_binding %r is not a known "
                                  "Naemon host; skipping passive result",
                                  r.name, host)
                    else:
                        svc = service_name(slugify_rule_name(r.name))
                        submit_passive_result(valid_host, svc, rc, output)
                await db.commit()
            except Exception as e:
                log.exception("rule %s failed: %s", r.name, e)
                await db.rollback()


def _dispatch(rule: LogAlertRule, count: int, sample_rows: list,
              mode: str = "match", output: str = "") -> None:
    if mode == "absence":
        summary = (f"[{rule.severity}] log-freshness rule '{rule.name}' - only "
                   f"{count} log line(s) received (source may be down)")
    else:
        summary = (f"[{rule.severity}] log rule '{rule.name}' matched "
                   f"{count} times")
    payload = {
        "source":   "vexor-logs",
        "rule":     rule.name,
        "mode":     mode,
        "severity": rule.severity,
        "summary":  summary,
        "output":   output,
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

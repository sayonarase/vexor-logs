"""Background daemon: evaluate log alert rules every 30 s.

Polls VictoriaLogs for each enabled rule, counts matches in the configured
window, and POSTs to vexor-api's /v1/notifications/dispatch when the
threshold is exceeded. State (last_fired/last_count) is persisted in the
shared database via SQLAlchemy.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import _client
from .models import LogAlertRule

log = logging.getLogger("vexor.logs.evaluator")

POLL_INTERVAL = int(os.environ.get("VEXOR_LOG_ALERTS_INTERVAL", "30"))
NOTIFY_URL    = os.environ.get(
    "VEXOR_NOTIFY_URL", "http://127.0.0.1:8000/v1/notifications/dispatch"
)


async def _evaluate_once(session_factory) -> None:
    async with session_factory() as db:
        rules = (await db.execute(
            select(LogAlertRule).where(LogAlertRule.enabled.is_(True))
        )).scalars().all()
        for r in rules:
            try:
                start = (datetime.now(timezone.utc) - timedelta(seconds=r.window_sec)).isoformat()
                rows = _client.query(r.query, limit=r.threshold + 1, start=start)
                count = len(rows)
                r.last_count = count
                if count >= r.threshold:
                    r.last_fired = datetime.now(timezone.utc)
                    _dispatch(r, count, rows)
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
        "sample":   sample_rows[:3],
    }
    try:
        req = urllib.request.Request(
            NOTIFY_URL, method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
    except Exception as e:
        log.warning("notify dispatch failed: %s", e)


async def _amain() -> None:
    # Import inside main so a missing vexor-api context only fails at runtime.
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

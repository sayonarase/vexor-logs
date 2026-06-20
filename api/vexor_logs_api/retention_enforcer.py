"""Per-host log retention enforcer (run daily by a systemd timer).

Global retention (``-retentionPeriod``) is the efficient, native mechanism and
is the ceiling for every host. This enforcer layers per-host *shorter* retention
on top: for each ``log_retention_overrides`` row whose ``retention_days`` is less
than the global setting, it deletes that host's logs older than the override via
the VictoriaLogs ``/delete/run_task`` API.

The VictoriaLogs delete API rewrites stored data, so it is deliberately run at
most once per day, sequentially, and only when there is actually old data to
trim (a cheap pre-check query short-circuits no-op hosts).
"""
from __future__ import annotations
import asyncio
import fcntl
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from . import _client
from .models import LogRetentionOverride
from .settings_router import _current_settings

log = logging.getLogger("vexor.logs.retention.enforcer")

_LOCK_FILE = Path("/run/vexor/vexor-logs-retention.lock")
_OLD_LOWER_BOUND = "2000-01-01Z"


def _delete_older_than(host: str, cutoff_iso: str) -> str | None:
    """Start a VictoriaLogs delete task for a host's logs older than cutoff.

    Returns the task_id, or None on failure.
    """
    # Match this host's stream, bounded to logs strictly before the cutoff.
    logsql = f'host:"{host}" _time:[{_OLD_LOWER_BOUND}, {cutoff_iso}]'
    url = (f"{_client._base()}/delete/run_task"
           f"?filter={urllib.parse.quote(logsql)}")
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=60)  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body).get("task_id")
        except Exception:
            return body.strip() or "started"
    except Exception as e:
        log.error("delete task for host %s failed: %s", host, e)
        return None


def _has_old_data(host: str, cutoff_iso: str) -> bool:
    """Cheap pre-check: is there at least one log for this host before cutoff?"""
    q = f'host:"{host}" _time:[{_OLD_LOWER_BOUND}, {cutoff_iso}]'
    try:
        rows = _client.query(q, limit=1)
        return len(rows) > 0
    except Exception as e:
        log.warning("pre-check query for host %s failed: %s", host, e)
        # Be conservative: if we cannot tell, do nothing this cycle.
        return False


async def _enforce_once(session_factory) -> dict:
    settings = _current_settings()
    global_days = settings.retention_days
    summary = {"global_days": global_days, "trimmed": [], "skipped": [],
               "errors": []}
    async with session_factory() as db:
        overrides = (await db.execute(
            select(LogRetentionOverride)
        )).scalars().all()

    now = datetime.now(timezone.utc)
    for ov in overrides:
        if ov.retention_days >= global_days:
            # Override is >= global: global retention already drops the data
            # first, so there is nothing for us to do.
            summary["skipped"].append(
                {"host": ov.host, "reason": "override >= global retention"})
            continue
        cutoff = (now - timedelta(days=ov.retention_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        if not await asyncio.to_thread(_has_old_data, ov.host, cutoff):
            summary["skipped"].append(
                {"host": ov.host, "reason": "no data older than override"})
            continue
        task_id = await asyncio.to_thread(_delete_older_than, ov.host, cutoff)
        if task_id:
            log.info("trim host=%s keep=%dd cutoff=%s task=%s",
                     ov.host, ov.retention_days, cutoff, task_id)
            summary["trimmed"].append(
                {"host": ov.host, "retention_days": ov.retention_days,
                 "cutoff": cutoff, "task_id": task_id})
        else:
            summary["errors"].append({"host": ov.host})
    return summary


async def _amain() -> int:
    from app.database import async_session  # type: ignore
    res = await _enforce_once(async_session)
    log.info("retention enforcer done: %s", json.dumps(res, default=str))
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fp = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("another retention enforcer run is in progress; exiting")
        return
    try:
        asyncio.run(_amain())
    finally:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            fp.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

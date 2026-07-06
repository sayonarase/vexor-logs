"""Scheduling + composition for log digests/reports (feature F8).

``run_due`` is called once per evaluator iteration: it finds reports whose
``next_run`` has passed, composes a digest from VictoriaLogs (total volume,
top values of a field, optional error count) and delivers it as an internal
notification routed by the operator's notification policies. Also exposes
``build_digest`` / ``compute_next_run`` reused by the router's run/preview.
"""
from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import _client, notify
from .models import LogReport

log = logging.getLogger("vexor.logs.reports")

_SAFE_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


def compute_next_run(report: LogReport, from_dt: datetime) -> datetime:
    """Next fire time at/after ``from_dt`` for the report's schedule."""
    kind = (report.schedule_kind or "daily").lower()
    if kind == "interval":
        hours = max(1, int(report.interval_hours or 24))
        return from_dt + timedelta(hours=hours)
    hour = int(report.at_hour or 0)
    minute = int(report.at_minute or 0)
    candidate = from_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "weekly":
        dow = int(report.dow if report.dow is not None else 0)  # 0=Mon
        # advance to the right weekday
        days_ahead = (dow - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=days_ahead)
        if candidate <= from_dt:
            candidate = candidate + timedelta(days=7)
        return candidate
    # daily
    if candidate <= from_dt:
        candidate = candidate + timedelta(days=1)
    return candidate


def _count(query: str, start: str) -> int:
    rows = _client.query(f"{query} | stats count() as c", limit=1, start=start)
    for r in rows:
        if isinstance(r, dict) and "c" in r:
            try:
                return int(float(r["c"]))
            except (TypeError, ValueError):
                return 0
    return 0


def build_digest(report: LogReport) -> dict:
    """Compose the digest payload (synchronous; uses the VictoriaLogs client)."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(seconds=report.window_sec)).isoformat()
    base = (report.query or "*").strip() or "*"
    if "|" in base:
        base_for_stats = None  # cannot aggregate a piped query
    else:
        base_for_stats = base

    total = _count(base, start) if base_for_stats else 0

    top: list[dict] = []
    tf = report.top_field or "host"
    if base_for_stats and _SAFE_FIELD_RE.match(tf):
        q = (f"{base_for_stats} | stats by ({tf}) count() as c "
             f"| sort by (c) desc | limit 10")
        try:
            rows = _client.query(q, limit=50, start=start)
            for r in rows:
                if not isinstance(r, dict):
                    continue
                try:
                    c = int(float(r.get("c", 0) or 0))
                except (TypeError, ValueError):
                    c = 0
                top.append({"key": r.get(tf) or "(none)", "count": c})
        except Exception as e:
            log.warning("report %s top query failed: %s", report.name, e)

    errors = None
    if report.error_query:
        try:
            errors = _count(report.error_query, start)
        except Exception as e:
            log.warning("report %s error query failed: %s", report.name, e)

    hrs = report.window_sec / 3600.0
    win = f"{hrs:.0f}h" if hrs >= 1 else f"{report.window_sec}s"
    output = f"Log digest '{report.name}': {total:,} events in last {win}"
    if errors is not None:
        output += f", {errors:,} error(s)"
    lines = [output, ""]
    if top:
        lines.append(f"Top {tf}:")
        for t in top:
            lines.append(f"  {t['key']}: {t['count']:,}")
    long_output = "\n".join(lines)
    return {
        "output": output,
        "long_output": long_output,
        "total": total,
        "top": top,
        "errors": errors,
        "window": win,
    }


def deliver(report: LogReport) -> dict:
    """Compose + dispatch a report. Returns the digest dict."""
    digest = build_digest(report)
    notify.dispatch(
        host="vexor-logs",
        service=f"log-report:{report.name}",
        severity=report.severity or "info",
        output=digest["output"],
        long_output=digest["long_output"],
    )
    return digest


async def run_due(session_factory) -> None:
    async with session_factory() as db:
        reports = (await db.execute(
            select(LogReport).where(LogReport.enabled.is_(True))
        )).scalars().all()
        now = datetime.now(timezone.utc)
        for r in reports:
            try:
                nxt = r.next_run
                if nxt is not None and nxt.tzinfo is None:
                    nxt = nxt.replace(tzinfo=timezone.utc)
                if nxt is None:
                    # first-time scheduling: compute and store, don't fire now
                    r.next_run = compute_next_run(r, now)
                    await db.commit()
                    continue
                if nxt > now:
                    continue
                digest = await asyncio.to_thread(deliver, r)
                r.last_run = now
                r.last_output = digest["long_output"][:4000]
                r.next_run = compute_next_run(r, now)
                await db.commit()
            except Exception as e:
                log.exception("report %s failed: %s", r.name, e)
                await db.rollback()

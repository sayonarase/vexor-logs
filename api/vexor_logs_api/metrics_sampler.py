"""Sampling + threshold evaluation for log metrics (feature F3).

Called once per iteration by the log-alerts evaluator. For every enabled
metric whose sampling window has elapsed it runs the aggregation query, stores
a sample per group, updates ``last_value``/``last_state``, and — on a WARN/CRIT
state transition — sends an internal notification (respecting a cooldown).
Old samples are trimmed to keep the table bounded.
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete

from . import _client, notify
from .models import LogMetric, LogMetricSample
from .metrics_router import build_stats_query, parse_stats_rows

log = logging.getLogger("vexor.logs.metrics")

_SEV_TO_RC = {"info": 0, "ok": 0, "warning": 1, "critical": 2, "unknown": 3}
SAMPLE_RETENTION_DAYS = int(os.environ.get("VEXOR_LOG_METRIC_RETENTION_DAYS", "31"))
COOLDOWN = int(os.environ.get("VEXOR_LOG_METRIC_COOLDOWN_SEC", "600"))

# Per-process last-notify timestamps (best-effort dedup across the cooldown).
_last_notify: dict[int, datetime] = {}


def _state_for(value: float, warn, crit, severity: str) -> tuple[int, str]:
    sev = (severity or "warning").lower()
    if crit is not None and value >= crit:
        return 2, "CRITICAL"
    if warn is not None and value >= warn:
        return 1, "WARNING"
    if warn is None and crit is None:
        return 0, "OK"
    return 0, "OK"


async def sample_due(session_factory) -> None:
    async with session_factory() as db:
        metrics = (await db.execute(
            select(LogMetric).where(LogMetric.enabled.is_(True))
        )).scalars().all()
        now = datetime.now(timezone.utc)
        for m in metrics:
            try:
                last = m.last_run
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if last is not None and (now - last).total_seconds() < m.window_sec:
                    continue
                q = build_stats_query(m.query, m.group_by)
                start = (now - timedelta(seconds=m.window_sec)).isoformat()

                def _do():
                    return _client.query(q, limit=2000, start=start)
                rows = await asyncio.to_thread(_do)
                parsed = parse_stats_rows(rows, m.group_by, m.window_sec, m.agg)
                if not parsed:
                    parsed = [(None, 0.0)]

                for host, val in parsed:
                    db.add(LogMetricSample(metric_id=m.id, host=host, value=val, ts=now))

                agg_value = max((v for _, v in parsed), default=0.0)
                worst_host = None
                for host, val in parsed:
                    if val == agg_value:
                        worst_host = host
                        break
                m.last_value = agg_value
                m.last_run = now

                rc, label = _state_for(agg_value, m.warn_threshold,
                                        m.crit_threshold, m.severity)
                prev = m.last_state
                m.last_state = rc

                # Notify on transition into a bad state (WARN/CRIT), with cooldown.
                if rc in (1, 2) and prev != rc:
                    ln = _last_notify.get(m.id)
                    if ln is None or (now - ln).total_seconds() >= COOLDOWN:
                        _last_notify[m.id] = now
                        unit = f" {m.unit}" if m.unit else ""
                        where = f" on {worst_host}" if worst_host else ""
                        thr = m.crit_threshold if rc == 2 else m.warn_threshold
                        out = (f"{label}: log metric '{m.name}' = "
                               f"{agg_value:g}{unit}{where} (>= {thr:g})")
                        sev = "critical" if rc == 2 else (m.severity or "warning")
                        await asyncio.to_thread(
                            notify.dispatch,
                            m.host_binding or worst_host or "vexor-logs",
                            f"log-metric:{m.name}", sev, out)

                # Trim old samples for this metric.
                cutoff = now - timedelta(days=SAMPLE_RETENTION_DAYS)
                await db.execute(
                    delete(LogMetricSample).where(
                        LogMetricSample.metric_id == m.id,
                        LogMetricSample.ts < cutoff))
                await db.commit()
            except Exception as e:
                log.exception("metric %s failed: %s", m.name, e)
                await db.rollback()

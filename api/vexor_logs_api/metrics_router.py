"""Log-derived metrics (feature F3).

Turns a log query into a periodically-sampled, graphable metric with optional
warn/crit thresholds. Sampling + threshold evaluation runs in the log-alerts
evaluator (see ``metrics_sampler.py``); this router provides CRUD, a stored
time-series (``/{id}/series``) and an ad-hoc ``/test`` preview.
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, delete

from sqlalchemy.ext.asyncio import AsyncSession

from . import _client
from .models import LogMetric, LogMetricSample

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_operator, require_viewer  # type: ignore
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


router = APIRouter(prefix="/api/v1/logs/metrics", tags=["logs-metrics"])

_SAFE_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_RANGE_RE = re.compile(r"^(\d{1,5})([smhdw])$")
_RANGE_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _range_seconds(value: str, default: int = 86400) -> int:
    m = _RANGE_RE.match((value or "").strip())
    if not m:
        return default
    return int(m.group(1)) * _RANGE_UNIT[m.group(2)]


def _validate(body: "MetricIn") -> None:
    if "|" in body.query:
        raise HTTPException(
            400, "metric query must be a plain filter (no '|' pipes); "
                 "the aggregation is added automatically")
    if body.group_by and not _SAFE_FIELD_RE.match(body.group_by):
        raise HTTPException(400, f"invalid group_by field: {body.group_by!r}")


class MetricIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    query: str = Field("*", min_length=1)
    agg: str = Field("count", pattern="^(count|rate)$")
    window_sec: int = Field(300, ge=30, le=86400)
    group_by: Optional[str] = Field(None, max_length=64)
    unit: Optional[str] = Field(None, max_length=32)
    enabled: bool = True
    warn_threshold: Optional[float] = None
    crit_threshold: Optional[float] = None
    severity: str = Field("warning", max_length=32)
    host_binding: Optional[str] = Field(None, max_length=255)


class MetricOut(MetricIn):
    id: int
    last_value: Optional[float] = None
    last_state: Optional[int] = None
    last_run: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _to_out(m: LogMetric) -> MetricOut:
    return MetricOut(
        id=m.id, name=m.name, query=m.query, agg=m.agg, window_sec=m.window_sec,
        group_by=m.group_by, unit=m.unit, enabled=m.enabled,
        warn_threshold=m.warn_threshold, crit_threshold=m.crit_threshold,
        severity=m.severity, host_binding=m.host_binding,
        last_value=m.last_value, last_state=m.last_state, last_run=m.last_run,
        created_at=m.created_at, updated_at=m.updated_at,
    )


@router.get("", response_model=list[MetricOut])
async def list_metrics(db: AsyncSession = Depends(get_db), _=Depends(require_viewer)):
    rs = (await db.execute(select(LogMetric).order_by(LogMetric.name))).scalars().all()
    return [_to_out(m) for m in rs]


@router.post("", response_model=MetricOut)
async def create_metric(body: MetricIn, db: AsyncSession = Depends(get_db),
                        _=Depends(require_operator)):
    _validate(body)
    m = LogMetric(**body.model_dump())
    db.add(m)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(m)
    return _to_out(m)


@router.put("/{mid}", response_model=MetricOut)
async def update_metric(mid: int, body: MetricIn, db: AsyncSession = Depends(get_db),
                        _=Depends(require_operator)):
    _validate(body)
    m = await db.get(LogMetric, mid)
    if not m:
        raise HTTPException(404, "not found")
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(m)
    return _to_out(m)


@router.delete("/{mid}")
async def delete_metric(mid: int, db: AsyncSession = Depends(get_db),
                        _=Depends(require_operator)):
    m = await db.get(LogMetric, mid)
    if m:
        await db.execute(delete(LogMetricSample).where(LogMetricSample.metric_id == mid))
        await db.delete(m)
        await db.commit()
    return {"ok": True}


@router.get("/{mid}/series")
async def series(mid: int, range: str = Query("24h"),
                 db: AsyncSession = Depends(get_db), _=Depends(require_viewer)) -> dict:
    m = await db.get(LogMetric, mid)
    if not m:
        raise HTTPException(404, "not found")
    since = datetime.now(timezone.utc) - timedelta(seconds=_range_seconds(range))
    rows = (await db.execute(
        select(LogMetricSample)
        .where(LogMetricSample.metric_id == mid, LogMetricSample.ts >= since)
        .order_by(LogMetricSample.ts)
    )).scalars().all()
    return {
        "metric": _to_out(m),
        "samples": [
            {"ts": s.ts.isoformat() if s.ts else None, "host": s.host, "value": s.value}
            for s in rows
        ],
    }


def build_stats_query(query: str, group_by: Optional[str]) -> str:
    base = (query or "*").strip() or "*"
    if group_by:
        return f"{base} | stats by ({group_by}) count() as c"
    return f"{base} | stats count() as c"


def parse_stats_rows(rows: list, group_by: Optional[str], window_sec: int,
                     agg: str) -> list[tuple[Optional[str], float]]:
    out: list[tuple[Optional[str], float]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            val = float(r.get("c", 0) or 0)
        except (TypeError, ValueError):
            val = 0.0
        if agg == "rate" and window_sec > 0:
            val = val / window_sec
        host = r.get(group_by) if group_by else None
        out.append((host, val))
    return out


@router.post("/test")
async def test_metric(body: MetricIn, _=Depends(require_operator)) -> dict:
    _validate(body)
    q = build_stats_query(body.query, body.group_by)
    start = (datetime.now(timezone.utc) - timedelta(seconds=body.window_sec)).isoformat()
    try:
        rows = _client.query(q, limit=1000, start=start)
    except Exception as e:
        raise HTTPException(502, f"victorialogs: {e}")
    parsed = parse_stats_rows(rows, body.group_by, body.window_sec, body.agg)
    return {
        "query": q,
        "window_sec": body.window_sec,
        "results": [{"host": h, "value": v} for h, v in parsed],
        "total": sum(v for _, v in parsed),
    }

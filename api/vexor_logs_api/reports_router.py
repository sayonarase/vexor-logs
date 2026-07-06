"""Scheduled log digests / reports (feature F8) — CRUD + run/preview."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .models import LogReport
from . import reports_scheduler

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_operator, require_viewer  # type: ignore
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


router = APIRouter(prefix="/api/v1/logs/reports", tags=["logs-reports"])


class ReportIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    query: str = Field("*", min_length=1)
    window_sec: int = Field(86400, ge=300, le=2678400)
    schedule_kind: str = Field("daily", pattern="^(daily|weekly|interval)$")
    at_hour: int = Field(7, ge=0, le=23)
    at_minute: int = Field(0, ge=0, le=59)
    dow: Optional[int] = Field(None, ge=0, le=6)
    interval_hours: Optional[int] = Field(None, ge=1, le=744)
    top_field: str = Field("host", max_length=64)
    error_query: Optional[str] = None
    severity: str = Field("info", max_length=32)
    recipients: Optional[str] = Field(None, max_length=255)


class ReportOut(ReportIn):
    id: int
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    last_output: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _to_out(r: LogReport) -> ReportOut:
    return ReportOut(
        id=r.id, name=r.name, enabled=r.enabled, query=r.query,
        window_sec=r.window_sec, schedule_kind=r.schedule_kind,
        at_hour=r.at_hour, at_minute=r.at_minute, dow=r.dow,
        interval_hours=r.interval_hours, top_field=r.top_field,
        error_query=r.error_query, severity=r.severity, recipients=r.recipients,
        last_run=r.last_run, next_run=r.next_run, last_output=r.last_output,
        created_at=r.created_at, updated_at=r.updated_at,
    )


@router.get("", response_model=list[ReportOut])
async def list_reports(db: AsyncSession = Depends(get_db), _=Depends(require_viewer)):
    rs = (await db.execute(select(LogReport).order_by(LogReport.name))).scalars().all()
    return [_to_out(r) for r in rs]


@router.post("", response_model=ReportOut)
async def create_report(body: ReportIn, db: AsyncSession = Depends(get_db),
                        _=Depends(require_operator)):
    r = LogReport(**body.model_dump())
    r.next_run = reports_scheduler.compute_next_run(r, datetime.now(timezone.utc))
    db.add(r)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(r)
    return _to_out(r)


@router.put("/{rid}", response_model=ReportOut)
async def update_report(rid: int, body: ReportIn, db: AsyncSession = Depends(get_db),
                        _=Depends(require_operator)):
    r = await db.get(LogReport, rid)
    if not r:
        raise HTTPException(404, "not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    r.next_run = reports_scheduler.compute_next_run(r, datetime.now(timezone.utc))
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(r)
    return _to_out(r)


@router.delete("/{rid}")
async def delete_report(rid: int, db: AsyncSession = Depends(get_db),
                        _=Depends(require_operator)):
    r = await db.get(LogReport, rid)
    if r:
        await db.delete(r)
        await db.commit()
    return {"ok": True}


@router.post("/{rid}/run", response_model=ReportOut)
async def run_report(rid: int, db: AsyncSession = Depends(get_db),
                     _=Depends(require_operator)):
    """Compose + deliver the digest now, and reschedule the next run."""
    r = await db.get(LogReport, rid)
    if not r:
        raise HTTPException(404, "not found")
    now = datetime.now(timezone.utc)
    digest = await asyncio.to_thread(reports_scheduler.deliver, r)
    r.last_run = now
    r.last_output = digest["long_output"][:4000]
    r.next_run = reports_scheduler.compute_next_run(r, now)
    await db.commit()
    await db.refresh(r)
    return _to_out(r)


@router.post("/preview")
async def preview_report(body: ReportIn, _=Depends(require_operator)) -> dict:
    """Compose the digest without sending it (dry run)."""
    r = LogReport(**body.model_dump())
    digest = await asyncio.to_thread(reports_scheduler.build_digest, r)
    return digest

"""Per-host log retention overrides.

Global retention (``-retentionPeriod`` on VictoriaLogs) is the ceiling that
applies to every host and is the efficient, native mechanism. An override lets
an operator trim a single host's logs SOONER than the global setting. The daily
``vexor-logs-retention-enforcer`` reads these rows and deletes each host's logs
older than its ``retention_days`` via the VictoriaLogs ``/delete`` API.

Keeping a host LONGER than the rest is done by raising the global retention —
VictoriaLogs OSS drops whole per-day partitions, so the global is always the
maximum any host can be retained for.
"""
from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LogRetentionOverride
from .settings_router import _current_settings

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_admin, require_viewer  # type: ignore
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_admin(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


log = logging.getLogger("vexor.logs.retention")
router = APIRouter(prefix="/api/v1/logs/retention", tags=["logs-retention"])


class OverrideIn(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    retention_days: int = Field(..., ge=1, le=3650)
    note: Optional[str] = Field(None, max_length=255)


class OverrideOut(BaseModel):
    id: int
    host: str
    retention_days: int
    note: Optional[str] = None
    exceeds_global: bool = False

    class Config:
        from_attributes = True


def _to_out(row: LogRetentionOverride, global_days: int) -> OverrideOut:
    return OverrideOut(
        id=row.id, host=row.host, retention_days=row.retention_days,
        note=row.note,
        # An override longer than global has no effect (global drops the data
        # first); flag it so the UI can warn.
        exceeds_global=row.retention_days >= global_days,
    )


@router.get("/overrides", response_model=list[OverrideOut])
async def list_overrides(db: AsyncSession = Depends(get_db),
                         _=Depends(require_viewer)):
    g = _current_settings().retention_days
    rows = (await db.execute(
        select(LogRetentionOverride).order_by(LogRetentionOverride.host)
    )).scalars().all()
    return [_to_out(r, g) for r in rows]


@router.post("/overrides", response_model=OverrideOut)
async def upsert_override(body: OverrideIn, db: AsyncSession = Depends(get_db),
                          _=Depends(require_admin)):
    g = _current_settings().retention_days
    existing = (await db.execute(
        select(LogRetentionOverride).where(LogRetentionOverride.host == body.host)
    )).scalar_one_or_none()
    if existing:
        existing.retention_days = body.retention_days
        existing.note = body.note
        row = existing
    else:
        row = LogRetentionOverride(
            host=body.host, retention_days=body.retention_days, note=body.note)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_out(row, g)


@router.delete("/overrides/{host}")
async def delete_override(host: str, db: AsyncSession = Depends(get_db),
                          _=Depends(require_admin)) -> dict:
    res = await db.execute(
        sa_delete(LogRetentionOverride).where(LogRetentionOverride.host == host)
    )
    await db.commit()
    if res.rowcount == 0:
        raise HTTPException(404, f"no override for host: {host}")
    return {"ok": True, "deleted": host}

"""CRUD for log alert rules."""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LogAlertRule

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_operator, require_viewer  # type: ignore
except Exception:  # pragma: no cover
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


router = APIRouter(prefix="/api/v1/log-alerts", tags=["log-alerts"])


class RuleIn(BaseModel):
    name: str
    query: str
    window_sec: int = Field(300, ge=10, le=86400)
    threshold: int = Field(1, ge=1)
    severity: str = "warning"
    notify_to: str = ""
    enabled: bool = True


class RuleOut(RuleIn):
    id: int
    last_fired: Optional[datetime] = None
    last_count: int = 0


def _to_out(r: LogAlertRule) -> RuleOut:
    return RuleOut(
        id=r.id, name=r.name, query=r.query, window_sec=r.window_sec,
        threshold=r.threshold, severity=r.severity, notify_to=r.notify_to,
        enabled=r.enabled, last_fired=r.last_fired, last_count=r.last_count,
    )


@router.get("", response_model=list[RuleOut])
async def list_rules(db: AsyncSession = Depends(get_db), _=Depends(require_viewer)):
    rs = (await db.execute(select(LogAlertRule).order_by(LogAlertRule.id))).scalars().all()
    return [_to_out(r) for r in rs]


@router.post("", response_model=RuleOut)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    r = LogAlertRule(**body.dict())
    db.add(r)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    await db.refresh(r)
    return _to_out(r)


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, body: RuleIn,
                      db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    r = await db.get(LogAlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="not found")
    for k, v in body.dict().items():
        setattr(r, k, v)
    await db.commit()
    await db.refresh(r)
    return _to_out(r)


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    await db.execute(delete(LogAlertRule).where(LogAlertRule.id == rule_id))
    await db.commit()
    return {"ok": True}

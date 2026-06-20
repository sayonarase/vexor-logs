"""CRUD for log alert rules (with host_binding for Naemon passive results)."""
from __future__ import annotations
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LogAlertRule, LogAlertEvent
from .naemon_passive import ensure_log_service, remove_log_service, slugify_rule_name, InvalidHostName, UnknownHost, NaemonReloadFailed

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_operator, require_viewer  # type: ignore
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


log = logging.getLogger("vexor.logs.alerts")
router = APIRouter(prefix="/api/v1/log-alerts", tags=["log-alerts"])


class RuleIn(BaseModel):
    name: str
    query: str
    window_sec: int = Field(300, ge=10, le=86400)
    threshold: int = Field(1, ge=1)
    severity: str = "warning"
    notify_to: str = ""
    host_binding: Optional[str] = None
    enabled: bool = True
    # log-check enhancements
    mode: str = Field("match", pattern="^(match|absence)$")
    warn_threshold: Optional[int] = Field(None, ge=0)
    crit_threshold: Optional[int] = Field(None, ge=0)
    level_filter: Optional[str] = None
    group_by_host: bool = False
    preset_id: Optional[str] = None


class RuleOut(RuleIn):
    id: int
    last_fired: Optional[datetime] = None
    last_count: int = 0


def _to_out(r: LogAlertRule) -> RuleOut:
    return RuleOut(
        id=r.id, name=r.name, query=r.query, window_sec=r.window_sec,
        threshold=r.threshold, severity=r.severity, notify_to=r.notify_to,
        host_binding=getattr(r, "host_binding", None),
        enabled=r.enabled,
        mode=getattr(r, "mode", None) or "match",
        warn_threshold=getattr(r, "warn_threshold", None),
        crit_threshold=getattr(r, "crit_threshold", None),
        level_filter=getattr(r, "level_filter", None),
        group_by_host=bool(getattr(r, "group_by_host", False)),
        preset_id=getattr(r, "preset_id", None),
        last_fired=r.last_fired, last_count=r.last_count,
    )


@router.get("", response_model=list[RuleOut])
async def list_rules(host: Optional[str] = None,
                     db: AsyncSession = Depends(get_db), _=Depends(require_viewer)):
    stmt = select(LogAlertRule).order_by(LogAlertRule.id)
    if host:
        stmt = stmt.where(LogAlertRule.host_binding == host)
    rs = (await db.execute(stmt)).scalars().all()
    return [_to_out(r) for r in rs]


class EventOut(BaseModel):
    id: int
    rule_id: Optional[int] = None
    rule_name: str
    host: Optional[str] = None
    mode: str = "match"
    severity: Optional[str] = None
    state: int
    prev_state: Optional[int] = None
    count: int = 0
    output: Optional[str] = None
    created_at: Optional[datetime] = None


@router.get("/history", response_model=list[EventOut])
async def list_history(host: Optional[str] = None,
                       rule_id: Optional[int] = None,
                       limit: int = 100,
                       db: AsyncSession = Depends(get_db),
                       _=Depends(require_viewer)):
    limit = max(1, min(limit, 1000))
    stmt = select(LogAlertEvent).order_by(LogAlertEvent.id.desc()).limit(limit)
    if host:
        stmt = stmt.where(LogAlertEvent.host == host)
    if rule_id is not None:
        stmt = stmt.where(LogAlertEvent.rule_id == rule_id)
    rs = (await db.execute(stmt)).scalars().all()
    return [
        EventOut(
            id=e.id, rule_id=e.rule_id, rule_name=e.rule_name, host=e.host,
            mode=e.mode, severity=e.severity, state=e.state,
            prev_state=e.prev_state, count=e.count, output=e.output,
            created_at=e.created_at,
        ) for e in rs
    ]


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
    if r.host_binding:
        try:
            ensure_log_service(r.host_binding, slugify_rule_name(r.name), r.name)
        except InvalidHostName as e:
            await db.execute(delete(LogAlertRule).where(LogAlertRule.id == r.id))
            await db.commit()
            raise HTTPException(400, f"invalid host_binding: {e}")
        except UnknownHost as e:
            await db.execute(delete(LogAlertRule).where(LogAlertRule.id == r.id))
            await db.commit()
            raise HTTPException(400, f"host_binding refers to unknown Naemon host: {e}")
        except NaemonReloadFailed as e:
            await db.execute(delete(LogAlertRule).where(LogAlertRule.id == r.id))
            await db.commit()
            raise HTTPException(409, f"naemon refused config: {e}")
        except Exception as e:
            log.warning("naemon ensure_log_service failed: %s", e)
    return _to_out(r)


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, body: RuleIn,
                      db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    r = await db.get(LogAlertRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="not found")
    # Snapshot current values so we can roll back if naemon rejects the change.
    snapshot = {c.name: getattr(r, c.name) for c in r.__table__.columns}
    old_host = r.host_binding
    old_slug = slugify_rule_name(r.name)
    for k, v in body.dict().items():
        setattr(r, k, v)
    # Adjust naemon service BEFORE commit so we can revert.
    try:
        if old_host and (old_host != r.host_binding or slugify_rule_name(r.name) != old_slug):
            remove_log_service(old_host, old_slug)
        if r.host_binding:
            ensure_log_service(r.host_binding, slugify_rule_name(r.name), r.name)
    except InvalidHostName as e:
        for k, v in snapshot.items(): setattr(r, k, v)
        raise HTTPException(400, f"invalid host_binding: {e}")
    except UnknownHost as e:
        for k, v in snapshot.items(): setattr(r, k, v)
        raise HTTPException(400, f"host_binding refers to unknown Naemon host: {e}")
    except NaemonReloadFailed as e:
        for k, v in snapshot.items(): setattr(r, k, v)
        raise HTTPException(409, f"naemon refused config: {e}")
    except Exception as e:
        log.warning("naemon service sync failed: %s", e)
    await db.commit()
    await db.refresh(r)
    return _to_out(r)


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    r = await db.get(LogAlertRule, rule_id)
    if r and r.host_binding:
        try:
            remove_log_service(r.host_binding, slugify_rule_name(r.name))
        except NaemonReloadFailed as e:
            log.warning("naemon reload failed after remove: %s", e)
        except Exception as e:
            log.warning("naemon remove_log_service failed: %s", e)
    await db.execute(delete(LogAlertRule).where(LogAlertRule.id == rule_id))
    await db.commit()
    return {"ok": True}

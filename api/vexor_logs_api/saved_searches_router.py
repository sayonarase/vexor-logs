"""Saved searches CRUD."""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LogSavedSearch

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import (  # type: ignore
        require_operator, require_viewer, get_principal,
    )
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore
    def get_principal(): return None  # type: ignore


router = APIRouter(prefix="/api/v1/logs/saved-searches", tags=["logs-saved-searches"])


class SavedIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    query: str
    time_range: str = Field("1h", max_length=64)


class SavedOut(SavedIn):
    id: int
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _to_out(r: LogSavedSearch) -> SavedOut:
    return SavedOut(
        id=r.id, name=r.name, query=r.query, time_range=r.time_range,
        created_by=r.created_by, created_at=r.created_at, updated_at=r.updated_at,
    )


def _principal_name(p) -> Optional[str]:
    if p is None:
        return None
    for attr in ("username", "name", "sub", "email"):
        v = getattr(p, attr, None)
        if v:
            return str(v)
    if isinstance(p, dict):
        for k in ("username", "name", "sub", "email"):
            if p.get(k):
                return str(p[k])
    return None


@router.get("", response_model=list[SavedOut])
async def list_saved(db: AsyncSession = Depends(get_db), _=Depends(require_viewer)):
    rs = (await db.execute(
        select(LogSavedSearch).order_by(LogSavedSearch.name)
    )).scalars().all()
    return [_to_out(r) for r in rs]


@router.post("", response_model=SavedOut)
async def create_saved(body: SavedIn, db: AsyncSession = Depends(get_db),
                       principal=Depends(get_principal),
                       _=Depends(require_operator)):
    r = LogSavedSearch(
        name=body.name, query=body.query, time_range=body.time_range,
        created_by=_principal_name(principal),
    )
    db.add(r)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(r)
    return _to_out(r)


@router.put("/{sid}", response_model=SavedOut)
async def update_saved(sid: int, body: SavedIn,
                       db: AsyncSession = Depends(get_db),
                       _=Depends(require_operator)):
    r = await db.get(LogSavedSearch, sid)
    if not r:
        raise HTTPException(404, "not found")
    r.name = body.name; r.query = body.query; r.time_range = body.time_range
    await db.commit()
    await db.refresh(r)
    return _to_out(r)


@router.delete("/{sid}")
async def delete_saved(sid: int, db: AsyncSession = Depends(get_db),
                       _=Depends(require_operator)):
    await db.execute(delete(LogSavedSearch).where(LogSavedSearch.id == sid))
    await db.commit()
    return {"ok": True}

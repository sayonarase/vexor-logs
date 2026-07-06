"""Log field-extraction / parse rules (feature F2).

CRUD for user-defined parse rules plus an ``expand`` endpoint that turns a base
LogsQL query into an augmented query with the enabled rules appended as
VictoriaLogs ``extract`` / ``extract_regexp`` pipes. The search UI toggles
"Apply field extraction", calls ``expand``, and runs the returned query.
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import _client
from .models import LogParseRule

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_operator, require_viewer  # type: ignore
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


router = APIRouter(prefix="/api/v1/logs/parse-rules", tags=["logs-parse-rules"])

_SAFE_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


def _quote(s: str) -> str:
    """Quote a LogsQL string literal (backslash + double-quote escaped)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def rule_to_pipe(source_field: str, pattern: str, pattern_type: str) -> str:
    field = source_field or "_msg"
    if not _SAFE_FIELD_RE.match(field):
        raise HTTPException(400, f"invalid source_field: {field!r}")
    verb = "extract_regexp" if (pattern_type or "pattern") == "regexp" else "extract"
    return f"{verb} {_quote(pattern)} from {field}"


def expand_query(base: str, rules: list[LogParseRule]) -> str:
    """Append extract pipes for the given rules to a base LogsQL query.

    Parse rules only make sense on a raw filter query; if the base query
    already contains a pipe (``|``) the rules are not applied (the result set
    is already transformed, e.g. by ``stats``).
    """
    base = (base or "").strip()
    if not base:
        base = "*"
    if "|" in base:
        return base
    pipes = [rule_to_pipe(r.source_field, r.pattern, r.pattern_type) for r in rules]
    if not pipes:
        return base
    return base + " | " + " | ".join(pipes)


class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    source_field: str = Field("_msg", max_length=64)
    pattern: str = Field(..., min_length=1)
    pattern_type: str = Field("pattern", pattern="^(pattern|regexp)$")
    enabled: bool = True
    sort_order: int = 100
    note: Optional[str] = Field(None, max_length=255)


class RuleOut(RuleIn):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _to_out(r: LogParseRule) -> RuleOut:
    return RuleOut(
        id=r.id, name=r.name, source_field=r.source_field, pattern=r.pattern,
        pattern_type=r.pattern_type, enabled=r.enabled, sort_order=r.sort_order,
        note=r.note, created_at=r.created_at, updated_at=r.updated_at,
    )


@router.get("", response_model=list[RuleOut])
async def list_rules(db: AsyncSession = Depends(get_db), _=Depends(require_viewer)):
    rs = (await db.execute(
        select(LogParseRule).order_by(LogParseRule.sort_order, LogParseRule.name)
    )).scalars().all()
    return [_to_out(r) for r in rs]


@router.post("", response_model=RuleOut)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    rule_to_pipe(body.source_field, body.pattern, body.pattern_type)  # validate
    r = LogParseRule(
        name=body.name, source_field=body.source_field, pattern=body.pattern,
        pattern_type=body.pattern_type, enabled=body.enabled,
        sort_order=body.sort_order, note=body.note,
    )
    db.add(r)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(r)
    return _to_out(r)


@router.put("/{rid}", response_model=RuleOut)
async def update_rule(rid: int, body: RuleIn, db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    rule_to_pipe(body.source_field, body.pattern, body.pattern_type)  # validate
    r = await db.get(LogParseRule, rid)
    if not r:
        raise HTTPException(404, "not found")
    r.name = body.name; r.source_field = body.source_field
    r.pattern = body.pattern; r.pattern_type = body.pattern_type
    r.enabled = body.enabled; r.sort_order = body.sort_order; r.note = body.note
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(r)
    return _to_out(r)


@router.delete("/{rid}")
async def delete_rule(rid: int, db: AsyncSession = Depends(get_db),
                      _=Depends(require_operator)):
    r = await db.get(LogParseRule, rid)
    if r:
        await db.delete(r)
        await db.commit()
    return {"ok": True}


@router.get("/expand")
async def expand(query: str = Query("*"), db: AsyncSession = Depends(get_db),
                 _=Depends(require_viewer)) -> dict:
    """Return the base query with all enabled parse rules appended as pipes."""
    rules = (await db.execute(
        select(LogParseRule)
        .where(LogParseRule.enabled.is_(True))
        .order_by(LogParseRule.sort_order, LogParseRule.name)
    )).scalars().all()
    return {"query": expand_query(query, rules)}


class PreviewIn(BaseModel):
    source_field: str = Field("_msg", max_length=64)
    pattern: str
    pattern_type: str = Field("pattern", pattern="^(pattern|regexp)$")
    query: str = "*"
    limit: int = Field(20, ge=1, le=200)


@router.post("/preview")
async def preview(body: PreviewIn, _=Depends(require_operator)) -> dict:
    """Run a single parse rule against recent logs and show extracted fields."""
    pipe = rule_to_pipe(body.source_field, body.pattern, body.pattern_type)
    base = (body.query or "*").strip() or "*"
    if "_time:" not in base:
        base = f"_time:1d {base}" if base != "*" else "_time:1d"
    q = f"{base} | {pipe} | limit {body.limit}"
    try:
        rows = _client.query(q, limit=body.limit)
    except Exception as e:
        raise HTTPException(502, f"victorialogs: {e}")
    return {"query": q, "rows": rows, "count": len(rows)}

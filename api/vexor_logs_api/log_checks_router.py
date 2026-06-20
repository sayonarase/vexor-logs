"""Log-check catalog + per-host bulk apply.

Turns the curated filter library (and a built-in dead-man / log-freshness
check) into ready-to-tick "log checks" that the Add Host wizard and the host
detail page offer. Applying a check creates a ``log_alert_rule`` bound to the
host, which the evaluator turns into a passive Naemon service - so it shows up
in dashboards, BSM, SLA reports and notifications like any other check.
"""
from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LogAlertRule
from .filter_library_router import _load_all as _load_filters
from .log_alerts_router import RuleOut, _to_out
from .naemon_passive import (
    ensure_log_service, remove_log_service, slugify_rule_name,
    InvalidHostName, UnknownHost, NaemonReloadFailed,
)

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_operator, require_viewer  # type: ignore
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


log = logging.getLogger("vexor.logs.checks")
router = APIRouter(prefix="/api/v1/log-checks", tags=["log-checks"])


# Built-in dead-man / log-freshness check. query="" means "all logs from the
# host"; at apply time it is scoped to host:"<host>" and evaluated in absence
# mode, so the service goes CRITICAL when the host stops shipping logs.
FRESHNESS_PRESET = {
    "id": "log-freshness",
    "name": "Log freshness (dead-man)",
    "description": "Alerts when a host stops sending logs - detects a crashed "
                   "agent, full disk, or a down host.",
    "query": "",
    "mode": "absence",
    "suggested_severity": "critical",
    "suggested_window_sec": 900,
    "suggested_threshold": 1,
    "tags": ["freshness", "dead-man", "availability"],
    "builtin": True,
}


class CheckPreset(BaseModel):
    id: str
    name: str
    description: str = ""
    query: str = ""
    mode: str = "match"
    suggested_severity: str = "warning"
    suggested_window_sec: int = 300
    suggested_threshold: int = 1
    tags: list[str] = Field(default_factory=list)
    builtin: bool = False


def _catalog() -> list[CheckPreset]:
    out: list[CheckPreset] = [CheckPreset(**FRESHNESS_PRESET)]
    for f in _load_filters():
        out.append(CheckPreset(
            id=f.id, name=f.name, description=f.description, query=f.query,
            mode="match", suggested_severity=f.suggested_severity,
            suggested_window_sec=f.suggested_window_sec,
            suggested_threshold=f.suggested_threshold, tags=f.tags,
        ))
    return out


@router.get("/catalog", response_model=list[CheckPreset])
def catalog(_=Depends(require_viewer)) -> list[CheckPreset]:
    """All log checks that can be applied to a host."""
    return _catalog()


def _scoped_query(host: str, base: str) -> str:
    """Scope a preset query to a single host stream.

    Agent-shipped logs (vector/fluent-bit) carry the host in the ``host``
    field, while the native syslog receiver parses it into ``hostname``.
    Match either so a host is covered regardless of how it ships logs.
    """
    base = (base or "").strip()
    hostsel = f'(host:"{host}" OR hostname:"{host}")'
    return f"{hostsel} ({base})" if base else hostsel


class CheckSelection(BaseModel):
    preset_id: str
    # optional overrides
    name: Optional[str] = None
    query: Optional[str] = None        # for preset_id == "custom"
    mode: Optional[str] = None
    severity: Optional[str] = None
    window_sec: Optional[int] = Field(None, ge=10, le=86400)
    threshold: Optional[int] = Field(None, ge=0)
    warn_threshold: Optional[int] = Field(None, ge=0)
    crit_threshold: Optional[int] = Field(None, ge=0)


class ForHostIn(BaseModel):
    host: str
    checks: list[CheckSelection]


@router.get("/host/{host}", response_model=list[RuleOut])
async def list_for_host(host: str, db: AsyncSession = Depends(get_db),
                        _=Depends(require_viewer)):
    rs = (await db.execute(
        select(LogAlertRule).where(LogAlertRule.host_binding == host)
        .order_by(LogAlertRule.id)
    )).scalars().all()
    return [_to_out(r) for r in rs]


@router.post("/for-host")
async def apply_for_host(body: ForHostIn, db: AsyncSession = Depends(get_db),
                         _=Depends(require_operator)) -> dict:
    """Create one passive log-check per selected preset, bound to ``host``."""
    cat = {c.id: c for c in _catalog()}
    created: list[dict] = []
    skipped: list[dict] = []
    warnings: list[str] = []

    for sel in body.checks:
        if sel.preset_id == "custom":
            if not sel.query or not sel.name:
                raise HTTPException(400, "custom check requires name and query")
            base_query = sel.query
            preset_name = sel.name
            mode = sel.mode or "match"
            severity = sel.severity or "warning"
            window = sel.window_sec or 300
            threshold = sel.threshold if sel.threshold is not None else 1
        else:
            p = cat.get(sel.preset_id)
            if not p:
                raise HTTPException(404, f"unknown preset: {sel.preset_id}")
            base_query = p.query
            preset_name = sel.name or p.name
            mode = sel.mode or p.mode
            severity = sel.severity or p.suggested_severity
            window = sel.window_sec or p.suggested_window_sec
            threshold = sel.threshold if sel.threshold is not None else p.suggested_threshold

        rule_name = f"{preset_name} @ {body.host}"
        # Idempotent: don't create duplicates for the same host+preset.
        existing = (await db.execute(
            select(LogAlertRule).where(LogAlertRule.name == rule_name)
        )).scalar_one_or_none()
        if existing:
            skipped.append({"name": rule_name, "reason": "already exists"})
            continue

        # Scope every check to this host's stream so it is per-host.
        query = _scoped_query(body.host, base_query)

        row = LogAlertRule(
            name=rule_name, query=query, window_sec=window,
            threshold=threshold, severity=severity, notify_to="",
            host_binding=body.host, enabled=True, mode=mode,
            warn_threshold=sel.warn_threshold, crit_threshold=sel.crit_threshold,
            preset_id=sel.preset_id,
        )
        db.add(row)
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            warnings.append(f"{rule_name}: db error: {e}")
            continue
        await db.refresh(row)
        try:
            ensure_log_service(body.host, slugify_rule_name(row.name), row.name)
        except InvalidHostName as e:
            await db.delete(row); await db.commit()
            raise HTTPException(400, f"invalid host: {e}")
        except UnknownHost as e:
            await db.delete(row); await db.commit()
            raise HTTPException(400, f"host is not a known Naemon host: {e}")
        except NaemonReloadFailed as e:
            await db.delete(row); await db.commit()
            raise HTTPException(409, f"naemon refused config: {e}")
        except Exception as e:
            log.exception("ensure_log_service failed for %s", rule_name)
            warnings.append(
                f"{rule_name}: saved but passive Naemon service not created: {e}")
        created.append(_to_out(row).dict())

    return {"ok": True, "created": created, "skipped": skipped,
            "warnings": warnings}

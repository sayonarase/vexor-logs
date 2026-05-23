"""Filter library — curated starter queries shipped as JSON in /etc/vexor/logs/filters/."""
from __future__ import annotations
import logging
import json
import os
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LogAlertRule, LogSavedSearch
from .log_alerts_router import RuleOut, _to_out as _alert_to_out
from .saved_searches_router import SavedOut, _to_out as _saved_to_out
from .naemon_passive import slugify_rule_name, ensure_log_service, InvalidHostName, UnknownHost, NaemonReloadFailed

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import (  # type: ignore
        require_admin, require_operator, require_viewer, get_principal,
    )
except Exception:
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_admin(): return None  # type: ignore
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore
    def get_principal(): return None  # type: ignore


log = logging.getLogger("vexor.logs.filter_library")

router = APIRouter(prefix="/api/v1/logs/filter-library", tags=["logs-filter-library"])

FILTERS_DIR = Path(os.environ.get("VEXOR_LOGS_FILTERS_DIR", "/etc/vexor/logs/filters"))


class FilterDef(BaseModel):
    id: str
    name: str
    description: str = ""
    query: str
    suggested_severity: str = "warning"
    suggested_window_sec: int = 300
    suggested_threshold: int = 1
    tags: list[str] = Field(default_factory=list)


def _load_all() -> list[FilterDef]:
    out: list[FilterDef] = []
    if not FILTERS_DIR.exists():
        return out
    for p in sorted(FILTERS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        data.setdefault("id", p.stem)
        try:
            out.append(FilterDef(**data))
        except Exception:
            continue
    return out


@router.get("", response_model=list[FilterDef])
def list_filters(_=Depends(require_viewer)) -> list[FilterDef]:
    return _load_all()


class InstallIn(BaseModel):
    id: str
    target: Literal["saved-search", "log-alert"] = "saved-search"
    name_override: Optional[str] = None
    host_binding: Optional[str] = None


@router.post("/install")
async def install(body: InstallIn, db: AsyncSession = Depends(get_db),
                  principal=Depends(get_principal),
                  _=Depends(require_operator)) -> dict:
    f = next((x for x in _load_all() if x.id == body.id), None)
    if not f:
        raise HTTPException(404, f"filter {body.id} not found")
    name = body.name_override or f.name
    if body.target == "saved-search":
        row = LogSavedSearch(name=name, query=f.query, time_range="1h",
                             created_by=str(getattr(principal, "username", "")
                                            or getattr(principal, "name", "")
                                            or "filter-library"))
        db.add(row)
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            raise HTTPException(400, str(e))
        await db.refresh(row)
        return {"ok": True, "kind": "saved-search", "item": _saved_to_out(row).dict()}
    # log-alert
    row = LogAlertRule(
        name=name, query=f.query,
        window_sec=f.suggested_window_sec,
        threshold=f.suggested_threshold,
        severity=f.suggested_severity,
        notify_to="",
        host_binding=body.host_binding,
        enabled=True,
    )
    db.add(row)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.refresh(row)
    naemon_warning = None
    if row.host_binding:
        try:
            ensure_log_service(row.host_binding, slugify_rule_name(row.name), row.name)
        except InvalidHostName as e:
            await db.delete(row); await db.commit()
            raise HTTPException(400, f"invalid host_binding: {e}")
        except UnknownHost as e:
            await db.delete(row); await db.commit()
            raise HTTPException(400, f"host_binding refers to unknown Naemon host: {e}")
        except NaemonReloadFailed as e:
            await db.delete(row); await db.commit()
            raise HTTPException(409, f"naemon refused config: {e}")
        except Exception as e:
            log.exception("ensure_log_service failed for rule id=%s host=%s",
                          row.id, row.host_binding)
            naemon_warning = (
                "Rule saved, but the passive Naemon service could not be created: "
                f"{type(e).__name__}: {e}. Alerts will not surface in Naemon "
                "until this is resolved."
            )
    out = {"ok": True, "kind": "log-alert", "item": _alert_to_out(row).dict()}
    if naemon_warning:
        out["warning"] = naemon_warning
    return out

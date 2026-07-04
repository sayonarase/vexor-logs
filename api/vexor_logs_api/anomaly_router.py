"""CRUD + catalog + on-demand test/explain for log anomaly monitors.

Routes (prefix /api/v1/log-anomalies):
  GET    /monitors            list monitors                (viewer)
  POST   /monitors            create a monitor             (admin)
  PUT    /monitors/{id}       update a monitor             (admin)
  DELETE /monitors/{id}       delete a monitor + templates (admin)
  POST   /monitors/{id}/test  run once now, preview        (admin)
  GET    /events              recent detections            (viewer)
  GET    /catalog             curated (Sigma-inspired) presets (viewer)
  POST   /catalog/{pid}/enable instantiate a preset        (admin)
  POST   /events/{id}/explain LLM plain-language enrichment (operator)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, desc

from . import anomaly as _anom
from .models import LogAnomalyMonitor, LogAnomalyEvent, LogAnomalyTemplate

try:
    from app.database import get_db  # type: ignore
    from app.services.auth import require_admin, require_operator, require_viewer  # type: ignore
except Exception:  # pragma: no cover - standalone import
    def get_db():  # type: ignore
        raise RuntimeError("vexor-api context required")
    def require_admin(): return None  # type: ignore
    def require_operator(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore

log = logging.getLogger("vexor.logs.anomaly.api")
router = APIRouter(prefix="/api/v1/log-anomalies", tags=["log-anomalies"])


# --------------------------------------------------------------------------
# Curated, Sigma-inspired detection presets. Each maps to a ready-to-run
# monitor. These are high-signal "known bad" patterns; the baseline/novelty
# engines cover the "unknown unknowns".
# --------------------------------------------------------------------------
CATALOG = [
    {
        "id": "ssh-bruteforce", "name": "SSH brute force",
        "category": "Security",
        "description": "Spike in failed SSH/PAM authentication attempts.",
        "kind": "baseline",
        "query": '_msg:~"(?i)(failed password|authentication failure|invalid user)"',
        "direction": "spike", "sensitivity": 3.0, "window_sec": 300,
        "severity": "warning",
    },
    {
        "id": "service-crash", "name": "Service crashes / panics",
        "category": "Reliability",
        "description": "Spike in segfaults, core dumps, kernel panics or stack traces.",
        "kind": "baseline",
        "query": '_msg:~"(?i)(segfault|core dumped|kernel panic|general protection fault|traceback \\(most recent)"',
        "direction": "spike", "sensitivity": 2.5, "window_sec": 300,
        "severity": "critical",
    },
    {
        "id": "oom", "name": "Out-of-memory kills",
        "category": "Reliability",
        "description": "Spike in OOM-killer activity / out-of-memory messages.",
        "kind": "baseline",
        "query": '_msg:~"(?i)(out of memory|oom-killer|oom_reaper|killed process)"',
        "direction": "spike", "sensitivity": 2.0, "window_sec": 600,
        "severity": "critical",
    },
    {
        "id": "disk-errors", "name": "Disk / filesystem errors",
        "category": "Hardware",
        "description": "Spike in I/O errors, ext4/xfs errors or SMART medium errors.",
        "kind": "baseline",
        "query": '_msg:~"(?i)(i/o error|ext4-fs error|xfs.*(error|corrupt)|medium error|ata.*failed)"',
        "direction": "spike", "sensitivity": 2.0, "window_sec": 600,
        "severity": "critical",
    },
    {
        "id": "priv-esc", "name": "Privilege escalation",
        "category": "Security",
        "description": "Spike in sudo command usage or su session openings.",
        "kind": "baseline",
        "query": '_msg:~"(?i)(sudo:.*command=|session opened for user root|COMMAND=/)"',
        "direction": "spike", "sensitivity": 3.0, "window_sec": 300,
        "severity": "warning",
    },
    {
        "id": "error-rate", "name": "Overall error-rate surge",
        "category": "Reliability",
        "description": "Sudden surge in error/critical level log volume vs the daily baseline.",
        "kind": "baseline",
        "query": '_msg:~"(?i)\\b(error|critical|fatal|emerg|panic|alert)\\b"',
        "direction": "spike", "sensitivity": 3.0, "window_sec": 300,
        "min_baseline": 5, "severity": "warning",
    },
    {
        "id": "log-silence", "name": "Log source went silent",
        "category": "Reliability",
        "description": "Log volume dropped far below the baseline (a source may be down).",
        "kind": "baseline",
        "query": "*",
        "direction": "drop", "sensitivity": 3.0, "window_sec": 300,
        "min_baseline": 10, "severity": "warning",
    },
    {
        "id": "new-templates", "name": "New / never-seen messages",
        "category": "Anomaly",
        "description": "A log message shape never observed before appears (unsupervised).",
        "kind": "novelty",
        "query": "*",
        "window_sec": 300, "severity": "info",
    },
]
_CATALOG_BY_ID = {c["id"]: c for c in CATALOG}


class MonitorIn(BaseModel):
    name: str
    kind: str = Field("baseline", pattern="^(baseline|novelty|watch)$")
    query: str = "*"
    enabled: bool = True
    window_sec: int = Field(300, ge=30, le=86400)
    baseline_sec: int = Field(86400, ge=300, le=2592000)
    sensitivity: float = Field(3.0, ge=0.5, le=20.0)
    direction: str = Field("both", pattern="^(spike|drop|both)$")
    min_baseline: float = Field(0, ge=0)
    min_interval_sec: int = Field(0, ge=0, le=86400)
    severity: str = Field("warning", pattern="^(info|warning|critical)$")
    host_binding: Optional[str] = None
    nl_question: Optional[str] = None


def _dump(m: LogAnomalyMonitor) -> dict:
    return {
        "id": m.id, "name": m.name, "kind": m.kind, "query": m.query,
        "enabled": bool(m.enabled), "window_sec": m.window_sec,
        "baseline_sec": m.baseline_sec, "sensitivity": m.sensitivity,
        "direction": m.direction, "min_baseline": m.min_baseline,
        "min_interval_sec": m.min_interval_sec, "severity": m.severity,
        "host_binding": m.host_binding, "nl_question": m.nl_question,
        "preset_id": m.preset_id, "last_state": m.last_state,
        "last_score": m.last_score,
        "last_run": m.last_run.isoformat() if m.last_run else None,
    }


@router.get("/monitors", dependencies=[Depends(require_viewer)])
async def list_monitors(db=Depends(get_db)):
    rows = (await db.execute(
        select(LogAnomalyMonitor).order_by(LogAnomalyMonitor.name)
    )).scalars().all()
    return {"monitors": [_dump(m) for m in rows]}


@router.post("/monitors", dependencies=[Depends(require_admin)])
async def create_monitor(body: MonitorIn, db=Depends(get_db)):
    exists = (await db.execute(
        select(LogAnomalyMonitor).where(LogAnomalyMonitor.name == body.name)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "a monitor with that name already exists")
    m = LogAnomalyMonitor(**body.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _dump(m)


@router.put("/monitors/{mid}", dependencies=[Depends(require_admin)])
async def update_monitor(mid: int, body: MonitorIn, db=Depends(get_db)):
    m = (await db.execute(
        select(LogAnomalyMonitor).where(LogAnomalyMonitor.id == mid)
    )).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "monitor not found")
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return _dump(m)


@router.delete("/monitors/{mid}", dependencies=[Depends(require_admin)])
async def delete_monitor(mid: int, db=Depends(get_db)):
    await db.execute(delete(LogAnomalyTemplate).where(LogAnomalyTemplate.monitor_id == mid))
    res = await db.execute(delete(LogAnomalyMonitor).where(LogAnomalyMonitor.id == mid))
    await db.commit()
    if not res.rowcount:
        raise HTTPException(404, "monitor not found")
    return {"deleted": mid}


@router.post("/monitors/{mid}/test", dependencies=[Depends(require_admin)])
async def test_monitor(mid: int, db=Depends(get_db)):
    m = (await db.execute(
        select(LogAnomalyMonitor).where(LogAnomalyMonitor.id == mid)
    )).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "monitor not found")
    try:
        det = await _anom.run_monitor(m, db)
    except Exception as exc:
        raise HTTPException(502, f"test run failed: {exc}")
    # A test run of a novelty monitor may have seeded templates; roll back so a
    # dry run never changes learned state.
    await db.rollback()
    return {
        "fired": det.fired, "rc": det.rc, "output": det.output,
        "score": det.score, "observed": det.observed,
        "baseline_mean": det.baseline_mean, "baseline_std": det.baseline_std,
        "sample": det.sample, "details": det.details,
    }


@router.get("/events", dependencies=[Depends(require_viewer)])
async def list_events(host: Optional[str] = None, monitor_id: Optional[int] = None,
                      limit: int = 100, db=Depends(get_db)):
    q = select(LogAnomalyEvent).order_by(desc(LogAnomalyEvent.created_at))
    if host:
        q = q.where(LogAnomalyEvent.host == host)
    if monitor_id:
        q = q.where(LogAnomalyEvent.monitor_id == monitor_id)
    q = q.limit(max(1, min(limit, 500)))
    rows = (await db.execute(q)).scalars().all()
    return {"events": [{
        "id": e.id, "monitor_id": e.monitor_id, "monitor_name": e.monitor_name,
        "kind": e.kind, "host": e.host, "severity": e.severity, "state": e.state,
        "prev_state": e.prev_state, "score": e.score, "observed": e.observed,
        "template": e.template, "sample": e.sample, "output": e.output,
        "llm_note": e.llm_note,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in rows]}


@router.get("/catalog", dependencies=[Depends(require_viewer)])
async def catalog(db=Depends(get_db)):
    enabled = {
        m.preset_id for m in (await db.execute(
            select(LogAnomalyMonitor).where(LogAnomalyMonitor.preset_id.isnot(None))
        )).scalars().all()
    }
    return {"presets": [{**c, "enabled": c["id"] in enabled} for c in CATALOG]}


@router.post("/catalog/{pid}/enable", dependencies=[Depends(require_admin)])
async def enable_preset(pid: str, db=Depends(get_db)):
    preset = _CATALOG_BY_ID.get(pid)
    if not preset:
        raise HTTPException(404, "unknown preset")
    existing = (await db.execute(
        select(LogAnomalyMonitor).where(LogAnomalyMonitor.preset_id == pid)
    )).scalar_one_or_none()
    if existing:
        return _dump(existing)
    m = LogAnomalyMonitor(
        name=preset["name"], kind=preset.get("kind", "baseline"),
        query=preset["query"], enabled=True,
        window_sec=preset.get("window_sec", 300),
        baseline_sec=preset.get("baseline_sec", 86400),
        sensitivity=preset.get("sensitivity", 3.0),
        direction=preset.get("direction", "both"),
        min_baseline=preset.get("min_baseline", 0),
        severity=preset.get("severity", "warning"),
        preset_id=pid,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _dump(m)


@router.post("/events/{eid}/explain", dependencies=[Depends(require_operator)])
async def explain_event(eid: int, db=Depends(get_db)):
    e = (await db.execute(
        select(LogAnomalyEvent).where(LogAnomalyEvent.id == eid)
    )).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "event not found")
    if e.llm_note:
        return {"llm_note": e.llm_note, "cached": True}
    try:
        from app.routers.llm_router import _generate  # type: ignore
    except Exception:
        raise HTTPException(503, "AI analysis is unavailable on this server build")
    system = (
        "You are a senior SRE. Explain a detected log anomaly in 2-4 short "
        "sentences: what likely happened, how serious it is, and the single "
        "most useful next check. Use only the supplied context; do not invent "
        "hostnames, IPs or values."
    )
    prompt = (
        f"Monitor: {e.monitor_name} (kind={e.kind}, host={e.host or 'n/a'})\n"
        f"Detection: {e.output}\n"
        f"Score: {e.score}  Observed: {e.observed}  "
        f"Baseline mean: {e.baseline_mean}\n"
        f"Sample log lines:\n{(e.sample or '(none)')[:6000]}"
    )
    try:
        note = await _generate(db, prompt, system, None, 400)
    except Exception as exc:
        raise HTTPException(502, f"AI analysis failed: {exc}")
    e.llm_note = (note or "").strip()
    await db.commit()
    return {"llm_note": e.llm_note, "cached": False}

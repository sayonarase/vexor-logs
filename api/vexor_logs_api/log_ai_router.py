"""AI-assisted log analysis.

Sends a sample of the operator's current log view to the system-wide LLM
provider (configured under Settings -> System -> External AI) and returns a
concise SRE-style triage. There are no per-user keys: the single provider/API
key configured by an admin is shared by everyone, exactly like the rest of the
LLM integration.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import _client

try:  # auth is provided by the host vexor-api process
    from app.services.auth import require_operator  # type: ignore
except Exception:  # pragma: no cover - standalone import
    def require_operator():  # type: ignore
        return None

try:  # reuse the core LLM dispatcher + DB session
    from app.routers.llm_router import _generate as _llm_generate  # type: ignore
    from app.database import get_db as _get_db  # type: ignore
    _LLM_OK = True
except Exception:  # pragma: no cover - logs plugin imported without core
    _LLM_OK = False

    async def _get_db():  # type: ignore
        yield None


router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

_SYSTEM_PROMPT = (
    "You are a senior site-reliability engineer triaging log output from a "
    "monitored IT environment. You are given a sample of raw log lines "
    "(newest first). Produce a SHORT, factual markdown report with exactly "
    "these sections:\n"
    "**Summary** - one or two sentences on overall health.\n"
    "**Notable events** - a bullet list of the most important errors or "
    "warnings; group repeated messages and state how many times each occurred.\n"
    "**Likely root cause** - your best hypothesis, or 'Inconclusive' if the "
    "sample does not support one.\n"
    "**Recommended next steps** - concrete, actionable checks or fixes.\n\n"
    "Rules: only use information present in the supplied lines; never invent "
    "log content, hostnames, IPs or values. If everything looks routine and "
    "healthy, say so plainly instead of inventing problems."
)

# Caps to keep the prompt within a sane token budget for any provider.
_MAX_LINES = 400
_MAX_CHARS = 14000


class AnalyzeReq(BaseModel):
    query: str = Field("*", description="LogsQL expression to analyse")
    start: Optional[str] = None
    end: Optional[str] = None
    limit: int = 300
    question: Optional[str] = Field(
        None, description="Optional question to focus the analysis on"
    )


def _fmt_row(row: dict) -> str:
    if isinstance(row, dict) and set(row.keys()) == {"_raw"}:
        return str(row["_raw"])[:500]
    t = row.get("_time") or row.get("time") or ""
    host = row.get("host") or row.get("hostname") or ""
    svc = (
        row.get("service")
        or row.get("syslog_identifier")
        or row.get("app")
        or row.get("unit")
        or ""
    )
    msg = row.get("_msg") or row.get("message") or row.get("msg") or ""
    prefix = " ".join(p for p in (str(t)[:19], str(host), str(svc)) if p)
    line = f"{prefix} | {msg}" if prefix else str(msg)
    return line[:500]


@router.post("/ai-analyze")
async def ai_analyze(body: AnalyzeReq, db=Depends(_get_db), _=Depends(require_operator)):
    """Summarise / triage the supplied log query with the configured LLM."""
    if not _LLM_OK:
        raise HTTPException(503, "AI analysis is unavailable on this server build")
    limit = max(1, min(body.limit, _MAX_LINES))
    try:
        rows = _client.query(body.query or "*", limit=limit, start=body.start, end=body.end)
    except Exception as exc:  # pragma: no cover - upstream/network
        raise HTTPException(502, f"log query failed: {exc}")
    if not rows:
        return {
            "analysis": "No log lines matched the current query and time range, "
            "so there is nothing to analyse.",
            "lines_analyzed": 0,
        }

    lines: List[str] = []
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        s = _fmt_row(row)
        if not s:
            continue
        total += len(s) + 1
        if total > _MAX_CHARS:
            break
        lines.append(s)

    sample = "\n".join(lines)
    focus = ""
    if body.question and body.question.strip():
        focus = f"\nThe operator specifically wants to know: {body.question.strip()}\n"
    prompt = (
        f"Log query: {body.query or '*'}\n"
        f"Lines supplied: {len(lines)} (newest first)\n"
        f"{focus}\n"
        f"--- BEGIN LOGS ---\n{sample}\n--- END LOGS ---"
    )

    try:
        out = await _llm_generate(db, prompt, _SYSTEM_PROMPT, None, 900)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(502, f"AI analysis failed: {exc}")

    return {"analysis": (out or "").strip(), "lines_analyzed": len(lines)}
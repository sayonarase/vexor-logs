"""Read-side router: query, tail and stream discovery."""
from __future__ import annotations
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from . import _client

try:
    from app.services.auth import require_viewer  # type: ignore
except Exception:  # standalone / test
    def require_viewer():  # type: ignore
        return None

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


@router.get("/health")
def health() -> dict:
    return {"ok": _client.health()}


@router.get("/query")
def query(
    query: str = Query(..., description="LogsQL expression"),
    limit: int = Query(500, ge=1, le=10000),
    start: Optional[str] = None,
    end: Optional[str] = None,
    _=Depends(require_viewer),
) -> dict:
    try:
        rows = _client.query(query, limit=limit, start=start, end=end)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    return {"rows": rows, "count": len(rows)}


@router.get("/streams")
def list_streams(_=Depends(require_viewer)) -> dict:
    return {"streams": _client.streams()}


@router.get("/tail")
def tail(query: str = Query(...), _=Depends(require_viewer)) -> StreamingResponse:
    """Server-sent events live-tail. Each event payload is one log line."""
    def gen():
        try:
            for chunk in _client.stream(query):
                # the VL tail endpoint emits JSONL; we re-emit as SSE
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    yield f"data: {line.decode('utf-8', 'replace') if isinstance(line, bytes) else line}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

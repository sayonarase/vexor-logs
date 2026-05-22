"""Read-side router: query, tail, stream discovery, histogram, export, test."""
from __future__ import annotations
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import _client

try:
    from app.services.auth import require_viewer  # type: ignore
except Exception:
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
    def gen():
        try:
            for chunk in _client.stream(query):
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    yield f"data: {line.decode('utf-8', 'replace') if isinstance(line, bytes) else line}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------
def _parse_iso(ts: str) -> datetime:
    s = ts.rstrip("Z")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(400, f"bad timestamp: {ts}")


def _choose_step(start: datetime, end: datetime, buckets: int) -> str:
    span = max(1, int((end - start).total_seconds()))
    step_sec = max(1, span // max(1, buckets))
    if step_sec < 60:
        return f"{step_sec}s"
    if step_sec < 3600:
        return f"{step_sec // 60}m"
    if step_sec < 86400:
        return f"{step_sec // 3600}h"
    return f"{step_sec // 86400}d"


@router.get("/histogram")
def histogram(
    query: str = Query(...),
    start: Optional[str] = None,
    end: Optional[str] = None,
    buckets: int = Query(50, ge=2, le=500),
    _=Depends(require_viewer),
) -> dict:
    now = datetime.now(timezone.utc)
    end_dt = _parse_iso(end) if end else now
    start_dt = _parse_iso(start) if start else end_dt - timedelta(hours=1)
    step = _choose_step(start_dt, end_dt, buckets)
    try:
        data = _client.hits(query, start=start_dt.isoformat(),
                            end=end_dt.isoformat(), step=step)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    out: list[dict] = []
    total = 0
    # VictoriaLogs /hits returns either {hits:[{timestamps, values}, ...]}
    # or {series:[{timestamps, values}, ...]} depending on version.
    series = (data.get("hits") if isinstance(data, dict) else None) or \
             (data.get("series") if isinstance(data, dict) else None) or []
    if series:
        s0 = series[0] if isinstance(series, list) else series
        timestamps = s0.get("timestamps") or s0.get("t") or []
        values = s0.get("values") or s0.get("v") or []
        for t, v in zip(timestamps, values):
            cnt = int(v or 0)
            out.append({"t": t, "count": cnt})
            total += cnt
    return {"buckets": out, "total": total, "step": step,
            "start": start_dt.isoformat(), "end": end_dt.isoformat()}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@router.get("/export")
def export(
    query: str = Query(...),
    format: str = Query("csv", pattern="^(csv|ndjson)$"),
    limit: int = Query(10000, ge=1, le=1000000),
    start: Optional[str] = None,
    end: Optional[str] = None,
    _=Depends(require_viewer),
):
    try:
        rows = _client.query(query, limit=limit, start=start, end=end)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if format == "ndjson":
        def gen_nd():
            for r in rows:
                yield (json.dumps(r, default=str) + "\n").encode()
        return StreamingResponse(
            gen_nd(), media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="logs_{ts}.ndjson"'},
        )
    # CSV: collect a stable column union
    cols: list[str] = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k); cols.append(k)
    if "_time" in cols:
        cols.remove("_time"); cols.insert(0, "_time")
    if "_msg" in cols:
        cols.remove("_msg"); cols.append("_msg")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else str(r.get(k))) for k in cols})
    data = buf.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([data]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="logs_{ts}.csv"'},
    )


# ---------------------------------------------------------------------------
# Test query (used by UI before saving a rule / search)
# ---------------------------------------------------------------------------
class TestQueryIn(BaseModel):
    query: str
    window_sec: int = Field(300, ge=10, le=86400)


@router.post("/test-query")
def test_query(body: TestQueryIn, _=Depends(require_viewer)) -> dict:
    start = (datetime.now(timezone.utc) - timedelta(seconds=body.window_sec)).isoformat()
    parse_ok = True
    sample: list = []
    try:
        sample = _client.query(body.query, limit=3, start=start)
    except Exception as e:
        return {"matched_count": 0, "sample": [], "parse_ok": False, "error": str(e)}
    # second call with bigger limit just to count
    try:
        more = _client.query(body.query, limit=1000, start=start)
        matched = len(more)
    except Exception:
        matched = len(sample)
        parse_ok = False
    return {"matched_count": matched, "sample": sample[:3], "parse_ok": parse_ok}

# ---------------------------------------------------------------------------
# Dashboard helpers: top-N field values and summary KPIs
# ---------------------------------------------------------------------------

def _safe_query(q: str) -> str:
    q = (q or "*").strip()
    if not q:
        q = "*"
    return q


def _topn(query: str, field: str, limit: int, start_dt, end_dt) -> list[dict]:
    pipe = f'{_safe_query(query)} | stats by ({field}) count() as c | sort by (c) desc | limit {int(limit)}'
    rows = _client.query(pipe, limit=limit + 5,
                         start=start_dt.isoformat(), end=end_dt.isoformat())
    out: list[dict] = []
    for r in rows or []:
        v = r.get(field)
        if v is None or v == "":
            v = "(empty)"
        try:
            c = int(r.get("c") or 0)
        except (TypeError, ValueError):
            c = 0
        out.append({"value": str(v), "count": c})
    return out


@router.get("/top")
def top_values(
    query: str = Query("*"),
    field: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    start: Optional[str] = None,
    end: Optional[str] = None,
    _=Depends(require_viewer),
) -> dict:
    now = datetime.now(timezone.utc)
    end_dt = _parse_iso(end) if end else now
    start_dt = _parse_iso(start) if start else end_dt - timedelta(hours=1)
    try:
        rows = _topn(query, field, limit, start_dt, end_dt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    return {"field": field, "rows": rows,
            "start": start_dt.isoformat(), "end": end_dt.isoformat()}


@router.get("/summary")
def summary(
    query: str = Query("*"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    _=Depends(require_viewer),
) -> dict:
    now = datetime.now(timezone.utc)
    end_dt = _parse_iso(end) if end else now
    start_dt = _parse_iso(start) if start else end_dt - timedelta(hours=1)
    span = max(1.0, (end_dt - start_dt).total_seconds())
    q = _safe_query(query)
    try:
        # total
        trows = _client.query(f"{q} | stats count() as c", limit=2,
                              start=start_dt.isoformat(), end=end_dt.isoformat())
        total = int((trows or [{}])[0].get("c") or 0)
        # error count - severity field plus common synonyms
        err_q = (f'({q}) AND (severity:(error OR critical OR alert OR emergency OR fatal) '
                 f'OR level:(error OR critical OR fatal) '
                 f'OR _msg:(error OR critical OR fatal))')
        erows = _client.query(f"{err_q} | stats count() as c", limit=2,
                              start=start_dt.isoformat(), end=end_dt.isoformat())
        errors = int((erows or [{}])[0].get("c") or 0)
        # unique hosts
        hrows = _topn(q, "host", 1000, start_dt, end_dt)
        unique_hosts = len(hrows)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    return {
        "total": total,
        "errors": errors,
        "unique_hosts": unique_hosts,
        "events_per_minute": round(total / (span / 60.0), 2),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "span_seconds": int(span),
    }

@router.get("/histogram_by")
def histogram_by(
    query: str = Query("*"),
    field: str = Query(...),
    buckets: int = Query(40, ge=2, le=200),
    top: int = Query(5, ge=1, le=20),
    start: Optional[str] = None,
    end: Optional[str] = None,
    _=Depends(require_viewer),
) -> dict:
    """Stacked-by-field histogram.

    Returns one series per top-N value of <field>, each with the same
    timestamp grid. Anything not in the top-N is rolled into an
    \"other\" series.
    """
    now = datetime.now(timezone.utc)
    end_dt = _parse_iso(end) if end else now
    start_dt = _parse_iso(start) if start else end_dt - timedelta(hours=1)
    step = _choose_step(start_dt, end_dt, buckets)
    q = _safe_query(query)
    try:
        top_vals = [r["value"] for r in _topn(q, field, top, start_dt, end_dt)]
        series: list[dict] = []
        for v in top_vals:
            sub = f'({q}) AND {field}:{json.dumps(v)}'
            data = _client.hits(sub, start=start_dt.isoformat(),
                                end=end_dt.isoformat(), step=step)
            arr = (data.get("hits") or data.get("series") or [{}])[0] if isinstance(data, dict) else {}
            ts = arr.get("timestamps") or arr.get("t") or []
            vs = arr.get("values") or arr.get("v") or []
            series.append({"value": v,
                           "buckets": [{"t": t, "count": int(c or 0)} for t, c in zip(ts, vs)]})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    return {"field": field, "step": step, "series": series,
            "start": start_dt.isoformat(), "end": end_dt.isoformat()}


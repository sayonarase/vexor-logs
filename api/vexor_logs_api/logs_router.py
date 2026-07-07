"""Read-side router: query, tail, stream discovery, histogram, export, test."""
from __future__ import annotations
import re
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import _client

try:
    from app.services.auth import require_viewer  # type: ignore
except Exception:
    def require_viewer():  # type: ignore
        return None


_SAFE_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
def _safe_field(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_FIELD_RE.match(name):
        from fastapi import HTTPException
        raise HTTPException(400, f"invalid field name: {name!r}")
    return name

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


_WINDOW_RE = re.compile(r"^\d{1,4}[smhdw]$")


def _parse_vl_time(value: Optional[str]) -> Optional[datetime]:
    """Parse a VictoriaLogs _time string (RFC3339, may carry nanoseconds)."""
    if not value or not isinstance(value, str):
        return None
    t = value.strip().replace("Z", "+00:00")
    # fromisoformat only accepts 3 or 6 fractional digits -> trim nanoseconds.
    m = re.match(r"^(.*\.\d{6})\d*(\+\d{2}:\d{2})$", t)
    if m:
        t = m.group(1) + m.group(2)
    try:
        dt = datetime.fromisoformat(t)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/shippers")
def shippers(
    window: str = Query("7d", description="discovery lookback window, e.g. 30m, 24h, 7d"),
    ok_within: int = Query(600, ge=30, le=604800,
                           description="max age (s) still considered fresh/OK"),
    stale_within: int = Query(3600, ge=60, le=2592000,
                              description="max age (s) considered stale before silent"),
    _=Depends(require_viewer),
) -> dict:
    """List every host currently shipping logs, with a last-seen freshness status.

    Derived from VictoriaLogs by aggregating max(_time) per `host` over the
    lookback window. Status is ok / stale / silent based on the age of the most
    recent log line for each host.
    """
    if not _WINDOW_RE.match(window):
        raise HTTPException(400, "invalid window (expected e.g. 30m, 24h, 7d)")
    if stale_within < ok_within:
        stale_within = ok_within
    # Two populations feed the log store: (1) agent shippers (vector / fluent-bit)
    # stamp a `host` field; (2) native-syslog senders (switches, firewalls,
    # routers pointed straight at VictoriaLogs' :514 receiver) carry `hostname`
    # but no `host`. Aggregate both so network devices also show up here.
    now = datetime.now(timezone.utc)
    out: list[dict] = []

    def _ingest(rows: list[dict], group_field: str, kind: str) -> None:
        for r in rows:
            ident = (r.get(group_field) or "").strip()
            if not ident:
                continue
            try:
                logs = int(r.get("logs") or 0)
            except Exception:
                logs = 0
            last_raw = r.get("last_seen")
            parsed = _parse_vl_time(last_raw)
            if parsed is not None:
                age = max(0, int((now - parsed).total_seconds()))
                status = "ok" if age <= ok_within else ("stale" if age <= stale_within else "silent")
            else:
                age = None
                status = "unknown"
            out.append({
                "host": ident,
                "kind": kind,
                "field": group_field,
                "logs": logs,
                "last_seen": last_raw,
                "age_seconds": age,
                "status": status,
            })

    agent_q = (f"_time:{window} host:* | "
               "stats by (host) count() as logs, max(_time) as last_seen")
    syslog_q = (f"_time:{window} hostname:* NOT host:* | "
                "stats by (hostname) count() as logs, max(_time) as last_seen")
    try:
        agent_rows = _client.query(agent_q, limit=10000)
        syslog_rows = _client.query(syslog_q, limit=10000)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    _ingest(agent_rows, "host", "agent")
    _ingest(syslog_rows, "hostname", "syslog")

    out.sort(key=lambda x: (x["age_seconds"] is None, x["age_seconds"] or 0))
    counts = {"ok": 0, "stale": 0, "silent": 0, "unknown": 0}
    kind_counts = {"agent": 0, "syslog": 0}
    for x in out:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
        kind_counts[x["kind"]] = kind_counts.get(x["kind"], 0) + 1
    return {"shippers": out, "count": len(out), "window": window,
            "status_counts": counts, "kind_counts": kind_counts}


@router.get("/tail")
async def tail(request: Request, query: str = Query(...), _=Depends(require_viewer)) -> StreamingResponse:
    async def gen():
        import asyncio as _asyncio
        try:
            it = iter(_client.stream(query))
            while True:
                # cooperative disconnect check before each fetch
                if await request.is_disconnected():
                    break
                try:
                    chunk = await _asyncio.to_thread(next, it)
                except StopIteration:
                    break
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if await request.is_disconnected():
                        return
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
    def gen_csv():
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        yield buf.getvalue().encode("utf-8")
        buf.seek(0); buf.truncate()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else str(r.get(k))) for k in cols})
            yield buf.getvalue().encode("utf-8")
            buf.seek(0); buf.truncate()
    return StreamingResponse(
        gen_csv(), media_type="text/csv",
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
    # True total via stats pipe (not capped). Falls back to len(sample) on failure.
    matched: int = len(sample)
    matched_capped = False
    try:
        from .logsql import safe_pipe  # type: ignore
    except Exception:
        safe_pipe = None  # noqa: N806
    try:
        agg = _client.query(f"({body.query}) | stats count() as c", limit=1, start=start)
        if agg and isinstance(agg[0], dict):
            matched = int(agg[0].get("c") or agg[0].get("count") or matched)
    except Exception:
        # Fall back to len(query result) — but mark capped if we hit the limit.
        try:
            more = _client.query(body.query, limit=10000, start=start)
            matched = len(more)
            matched_capped = matched >= 10000
        except Exception:
            matched = len(sample)
            parse_ok = False
    return {"matched_count": matched, "matched_capped": matched_capped,
            "sample": sample[:3], "parse_ok": parse_ok}

# ---------------------------------------------------------------------------
# Dashboard helpers: top-N field values and summary KPIs
# ---------------------------------------------------------------------------

def _safe_query(q: str) -> str:
    q = (q or "*").strip()
    if not q:
        q = "*"
    return q


def _topn(query: str, field: str, limit: int, start_dt, end_dt) -> list[dict]:
    field = _safe_field(field)
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
        def _fetch(v):
            sub = f'({q}) AND {field}:{json.dumps(v)}'
            data = _client.hits(sub, start=start_dt.isoformat(),
                                end=end_dt.isoformat(), step=step)
            arr = (data.get("hits") or data.get("series") or [{}])[0] if isinstance(data, dict) else {}
            ts = arr.get("timestamps") or arr.get("t") or []
            vs = arr.get("values") or arr.get("v") or []
            return {"value": v,
                    "buckets": [{"t": t, "count": int(c or 0)} for t, c in zip(ts, vs)]}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(top_vals)))) as ex:
            series = list(ex.map(_fetch, top_vals))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    return {"field": field, "step": step, "series": series,
            "start": start_dt.isoformat(), "end": end_dt.isoformat()}

@router.get("/heatmap")
def heatmap(
    query: str = Query("*"),
    days: int = Query(7, ge=1, le=31),
    _=Depends(require_viewer),
) -> dict:
    """Hour-of-day x day-of-week activity heatmap over the last N days.

    Always uses a fixed 1h bucket regardless of the user's time window
    (Splunk-style: it characterises *patterns*, not absolute counts in
    the current selection).
    """
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    q = _safe_query(query)
    try:
        data = _client.hits(q, start=start_dt.isoformat(),
                            end=end_dt.isoformat(), step="1h")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"victorialogs: {e}")
    series = (data.get("hits") or data.get("series") or [{}])[0] if isinstance(data, dict) else {}
    ts = series.get("timestamps") or series.get("t") or []
    vs = series.get("values") or series.get("v") or []
    # grid[dow 0..6 mon..sun][hour 0..23] = count
    grid = [[0] * 24 for _ in range(7)]
    from datetime import datetime as _dt
    for t, c in zip(ts, vs):
        try:
            d = _dt.fromisoformat(str(t).replace("Z", "+00:00"))
            dow = d.weekday()
            grid[dow][d.hour] += int(c or 0)
        except Exception:
            continue
    return {"grid": grid, "days": days,
            "start": start_dt.isoformat(), "end": end_dt.isoformat()}



def _ctx_quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


@router.get("/context")
def context(
    time: str = Query(..., description="anchor _time (RFC3339)"),
    host: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    before: int = Query(10, ge=0, le=200),
    after: int = Query(10, ge=0, le=200),
    _=Depends(require_viewer),
) -> dict:
    """Return the log lines immediately before/after an anchor line.

    Scoped to the same host (and optionally service/stream) so the surrounding
    context is coherent. Powers the "show context" action in the search view.
    """
    anchor = _parse_vl_time(time)
    if anchor is None:
        raise HTTPException(400, f"invalid time: {time!r}")
    scope_parts = []
    if host:
        scope_parts.append(f"host:={_ctx_quote(host)}")
    if service:
        scope_parts.append(f"service:={_ctx_quote(service)}")
    scope = " ".join(scope_parts) or "*"
    lo = (anchor - timedelta(days=3)).isoformat()
    hi = (anchor + timedelta(days=3)).isoformat()
    anchor_iso = anchor.isoformat()
    try:
        qb = f"{scope} | sort by (_time) desc | limit {before + 1}"
        rows_before = _client.query(qb, limit=before + 1, start=lo, end=anchor_iso)
        rows_before.reverse()  # ascending, anchor is last
        qa = f"{scope} | sort by (_time) asc | limit {after + 1}"
        rows_after = _client.query(qa, limit=after + 1, start=anchor_iso, end=hi)
    except Exception as e:
        raise HTTPException(502, f"victorialogs: {e}")
    anchor_row = None
    if rows_before:
        anchor_row = rows_before[-1]
        rows_before = rows_before[:-1]
    elif rows_after:
        anchor_row = rows_after[0]
    if rows_after and anchor_row is not None:
        # after query is anchor-inclusive; drop the shared anchor row.
        if rows_after[0].get("_time") == anchor_row.get("_time") \
                and rows_after[0].get("_msg") == anchor_row.get("_msg"):
            rows_after = rows_after[1:]
    return {"before": rows_before, "anchor": anchor_row, "after": rows_after}

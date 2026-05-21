"""Tiny stdlib HTTP client for VictoriaLogs.

We avoid adding new Python deps to vexor-api, so we use urllib here. The
endpoint is read from /etc/vexor/logs.env via the VEXOR_LOGS_URL variable,
falling back to http://127.0.0.1:9428.
"""
from __future__ import annotations
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Iterator


def _base() -> str:
    return os.environ.get("VEXOR_LOGS_URL", "http://127.0.0.1:9428").rstrip("/")


def _open(path: str, params: dict[str, Any] | None = None, timeout: float = 30.0):
    url = f"{_base()}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    req = urllib.request.Request(url, headers={"Accept": "application/stream+json"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


def query(q: str, limit: int = 500, start: str | None = None,
          end: str | None = None) -> list[dict[str, Any]]:
    """Run a LogsQL query and return parsed results."""
    resp = _open("/select/logsql/query",
                 {"query": q, "limit": limit, "start": start, "end": end})
    out: list[dict[str, Any]] = []
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"_raw": line})
    return out


def stream(q: str) -> Iterator[bytes]:
    """Yield raw JSONL bytes for a live-tail style query."""
    resp = _open("/select/logsql/tail", {"query": q}, timeout=3600)
    for raw in resp:
        if raw:
            yield raw


def streams() -> list[dict[str, Any]]:
    try:
        resp = _open("/select/logsql/streams", {"query": "*"})
        data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and "values" in data:
            return data["values"]
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def hits(q: str, start: str | None = None, end: str | None = None,
         step: str = "1m") -> dict[str, Any]:
    """Return the /select/logsql/hits histogram envelope."""
    resp = _open("/select/logsql/hits",
                 {"query": q, "start": start, "end": end, "step": step})
    body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except Exception:
        return {"_raw": body}


def metrics_text() -> str:
    """Return the raw Prometheus exposition text from VictoriaLogs."""
    try:
        resp = _open("/metrics", timeout=10)
        return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def health() -> bool:
    try:
        r = urllib.request.urlopen(_base() + "/health", timeout=5)  # noqa: S310
        return r.status == 200
    except Exception:
        return False

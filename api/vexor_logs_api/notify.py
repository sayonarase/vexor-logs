"""Shared internal-notification dispatch for log metrics and reports.

Posts a ``DispatchEvent`` to vexor-api's internal notify endpoint using the
shared file token, mirroring the log-alert evaluator's ``_dispatch``. Used by
feature F3 (metric threshold breaches) and F8 (scheduled digests).
"""
from __future__ import annotations
import json
import logging
import os
import urllib.request

log = logging.getLogger("vexor.logs.notify")

NOTIFY_URL = os.environ.get(
    "VEXOR_NOTIFY_URL",
    "http://127.0.0.1:8080/api/v1/notify/dispatch-internal",
)
NOTIFY_TOKEN_FILE = os.environ.get("VEXOR_NOTIFY_TOKEN_FILE", "/etc/vexor/notify-token")


def _token() -> str:
    tok = os.environ.get("VEXOR_NOTIFY_TOKEN")
    if tok:
        return tok.strip()
    try:
        with open(NOTIFY_TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def dispatch(host: str, service: str, severity: str, output: str,
             long_output: str = "") -> bool:
    payload = {
        "host": host or "vexor-logs",
        "service": service,
        "severity": (severity or "warning").upper(),
        "output": output,
        "long_output": long_output,
        "attempt": 1,
    }
    headers = {"Content-Type": "application/json"}
    tok = _token()
    if tok:
        headers["X-Internal-Token"] = tok
    try:
        req = urllib.request.Request(
            NOTIFY_URL, method="POST",
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
        return True
    except Exception as e:
        log.warning("notify dispatch failed: %s", e)
        return False

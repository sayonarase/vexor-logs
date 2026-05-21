"""Advertise the "logs" module to the UI.

vexor-api may already expose /v1/modules; if so, this router is harmless
because it will be shadowed by the existing one. If it doesn't, this
router provides the endpoint the UI's sidebar gating logic checks.
"""
from __future__ import annotations
import os
from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["modules"])

_MODULES = ["logs"]  # this plugin implies "logs" is available


@router.get("/modules")
def modules() -> dict:
    extra = [m.strip() for m in os.environ.get("VEXOR_MODULES", "").split(",") if m.strip()]
    seen, ordered = set(), []
    for m in _MODULES + extra:
        if m not in seen:
            seen.add(m); ordered.append(m)
    return {"modules": ordered}

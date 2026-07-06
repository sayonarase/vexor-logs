"""Log enrichment: GeoIP country lookup (feature F4).

Resolves IP addresses appearing in log fields to a country using the DB-IP
Country Lite database shipped by ``vexor-geoip-update``. The lookup is done by
a dependency-free MMDB reader (``geoip.py``) so no new Python packages are
required in vexor-api's venv. The search UI batches the IPs it renders and
calls ``/logs/geoip`` to annotate rows with a country flag/name.

ASN enrichment is NOT provided: the shipped DB-IP Lite database is
country-only. An ASN database would be needed for that (follow-up).
"""
from __future__ import annotations
import re
from typing import Optional

from fastapi import APIRouter, Depends, Query

from . import geoip

try:
    from app.services.auth import require_viewer  # type: ignore
except Exception:
    def require_viewer():  # type: ignore
        return None


router = APIRouter(prefix="/api/v1/logs/geoip", tags=["logs-geoip"])

# Extract plausible IPv4 / IPv6 literals from free text.
_IP_RE = re.compile(
    r"\b(?:\d{1,3}(?:\.\d{1,3}){3})\b"
    r"|\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}\b")

_MAX_IPS = 512


@router.get("/status")
def status(_=Depends(require_viewer)) -> dict:
    return {"available": geoip.available(), "database": geoip.DEFAULT_DB,
            "kind": "country", "asn": False}


@router.get("")
def resolve(ips: str = Query("", description="comma or space separated IPs"),
            _=Depends(require_viewer)) -> dict:
    """Resolve a batch of IPs to countries.

    Accepts a comma/space separated list, or free text from which IP literals
    are extracted. Private / unknown addresses are simply omitted from the map.
    """
    if not geoip.available():
        return {"available": False, "results": {}}
    found: list[str] = []
    seen: set[str] = set()
    for m in _IP_RE.finditer(ips or ""):
        ip = m.group(0)
        if ip not in seen:
            seen.add(ip)
            found.append(ip)
        if len(found) >= _MAX_IPS:
            break
    return {"available": True, "results": geoip.lookup_many(found)}

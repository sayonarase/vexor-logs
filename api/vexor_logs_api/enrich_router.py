"""Log enrichment: GeoIP country lookup (feature F4).

Resolves IP addresses appearing in log fields to a country using the DB-IP
Country Lite database shipped by ``vexor-geoip-update``. The lookup is done by
a dependency-free MMDB reader (``geoip.py``) so no new Python packages are
required in vexor-api's venv. The search UI batches the IPs it renders and
calls ``/logs/geoip`` to annotate rows with a country flag/name.

ASN enrichment is also provided when the DB-IP ASN Lite database
(``/var/lib/vexor-geoip/dbip-asn-lite.mmdb``) is present: each IP is annotated
with its autonomous-system number and owning organisation. Both databases are
refreshed by ``vexor-download-stats --update-geo``.
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
    country_ok = geoip.available()
    asn_ok = geoip.asn_available()
    return {"available": country_ok or asn_ok,
            "country_available": country_ok,
            "asn_available": asn_ok,
            "database": geoip.DEFAULT_DB,
            "asn_database": geoip.ASN_DB,
            "kind": "country+asn", "asn": asn_ok}


@router.get("")
def resolve(ips: str = Query("", description="comma or space separated IPs"),
            _=Depends(require_viewer)) -> dict:
    """Resolve a batch of IPs to countries.

    Accepts a comma/space separated list, or free text from which IP literals
    are extracted. Private / unknown addresses are simply omitted from the map.
    """
    country_ok = geoip.available()
    asn_ok = geoip.asn_available()
    if not country_ok and not asn_ok:
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
    results: dict[str, dict] = {}
    if country_ok:
        for ip, info in geoip.lookup_many(found).items():
            results.setdefault(ip, {}).update(info)
    if asn_ok:
        for ip, info in geoip.asn_lookup_many(found).items():
            results.setdefault(ip, {}).update(info)
    return {"available": True, "results": results}

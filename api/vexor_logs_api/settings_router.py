"""GET / PUT /api/v1/logs/settings + GET /api/v1/logs/storage."""
from __future__ import annotations
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, AnyHttpUrl, model_validator

from . import _client

try:
    from app.services.auth import require_admin, require_viewer  # type: ignore
except Exception:
    def require_admin(): return None  # type: ignore
    def require_viewer(): return None  # type: ignore


log = logging.getLogger("vexor.logs.settings")
router = APIRouter(prefix="/api/v1/logs", tags=["logs-settings"])

ENV_FILE = Path(os.environ.get("VEXOR_LOGS_ENV", "/etc/vexor/logs.env"))
STORAGE_DIR = Path(os.environ.get("VEXOR_LOGS_STORAGE",
                                  "/var/lib/vexor/victorialogs"))
DEFAULT_RETENTION = 90


class Settings(BaseModel):
    retention_days: int = Field(DEFAULT_RETENTION, ge=1, le=3650)
    # Disk-based retention (VictoriaLogs drops oldest per-day partitions when the
    # cap is exceeded). "none" | "bytes" | "percent" — bytes/percent are mutually
    # exclusive in VictoriaLogs. retention_days still applies independently.
    disk_mode: str = Field("none", pattern="^(none|bytes|percent)$")
    disk_bytes: Optional[str] = None        # e.g. "100GiB"
    disk_percent: Optional[int] = Field(None, ge=1, le=100)
    # Native VictoriaLogs syslog receiver (RFC3164/5424, auto-parsed into
    # hostname/app_name/priority/severity/facility fields). Off by default.
    syslog_enabled: bool = False
    syslog_udp_port: int = Field(514, ge=1, le=65535)
    syslog_tcp_port: int = Field(514, ge=1, le=65535)
    vexor_logs_url: Optional[AnyHttpUrl] = None

    @model_validator(mode="after")
    def _check_disk(self) -> "Settings":
        if self.disk_mode == "bytes":
            if not self.disk_bytes or not _DISK_BYTES_RE.match(self.disk_bytes.strip()):
                raise ValueError(
                    "disk_bytes must be a size like '100GiB', '500MB', '2TiB'")
        elif self.disk_mode == "percent":
            if self.disk_percent is None:
                raise ValueError("disk_percent (1-100) required when disk_mode=percent")
        return self


_DISK_BYTES_RE = re.compile(r"^\d+(\.\d+)?\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)?$", re.I)


def _read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _write_env(updates: dict[str, str]) -> None:
    existing: list[str] = []
    if ENV_FILE.exists():
        existing = ENV_FILE.read_text().splitlines()
    keys_set = set(updates.keys())
    new_lines: list[str] = []
    seen: set[str] = set()
    for raw in existing:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", raw)
        if m and m.group(1) in keys_set:
            k = m.group(1)
            new_lines.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            new_lines.append(raw)
    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(new_lines).rstrip() + "\n")


def _current_settings() -> Settings:
    env = _read_env()
    try:
        rd = int(env.get("VEXOR_LOGS_RETENTION_DAYS", str(DEFAULT_RETENTION)))
    except ValueError:
        rd = DEFAULT_RETENTION
    disk_mode = env.get("VEXOR_LOGS_DISK_MODE", "none")
    if disk_mode not in ("none", "bytes", "percent"):
        disk_mode = "none"
    disk_percent: Optional[int] = None
    try:
        if env.get("VEXOR_LOGS_DISK_PERCENT"):
            disk_percent = int(env["VEXOR_LOGS_DISK_PERCENT"])
    except ValueError:
        disk_percent = None
    udp = env.get("VEXOR_LOGS_SYSLOG_UDP", "")
    tcp = env.get("VEXOR_LOGS_SYSLOG_TCP", "")
    syslog_enabled = bool(udp or tcp)

    def _port(addr: str, default: int) -> int:
        m = re.search(r":(\d+)\s*$", addr)
        try:
            return int(m.group(1)) if m else default
        except ValueError:
            return default

    return Settings(
        retention_days=rd,
        disk_mode=disk_mode,
        disk_bytes=env.get("VEXOR_LOGS_DISK_BYTES") or None,
        disk_percent=disk_percent,
        syslog_enabled=syslog_enabled,
        syslog_udp_port=_port(udp, 514),
        syslog_tcp_port=_port(tcp, 514),
        vexor_logs_url=env.get("VEXOR_LOGS_URL", "http://127.0.0.1:9428"),
    )


@router.get("/settings", response_model=Settings)
def get_settings(_=Depends(require_viewer)) -> Settings:
    return _current_settings()


@router.put("/settings", response_model=Settings)
def put_settings(body: Settings, _=Depends(require_admin)) -> Settings:
    updates = {
        "VEXOR_LOGS_RETENTION_DAYS": str(body.retention_days),
        "VEXOR_LOGS_DISK_MODE": body.disk_mode,
        "VEXOR_LOGS_DISK_BYTES": body.disk_bytes if body.disk_mode == "bytes" and body.disk_bytes else "",
        "VEXOR_LOGS_DISK_PERCENT": str(body.disk_percent) if body.disk_mode == "percent" and body.disk_percent else "",
        "VEXOR_LOGS_SYSLOG_UDP": f":{body.syslog_udp_port}" if body.syslog_enabled else "",
        "VEXOR_LOGS_SYSLOG_TCP": f":{body.syslog_tcp_port}" if body.syslog_enabled else "",
    }
    if body.vexor_logs_url:
        updates["VEXOR_LOGS_URL"] = str(body.vexor_logs_url)
    try:
        _write_env(updates)
    except PermissionError as e:
        raise HTTPException(500, f"cannot write {ENV_FILE}: {e}")
    # Restart victorialogs so the retention flags are re-applied. We run as the
    # vexor user; non-root systemctl restart works via the polkit rule
    # /etc/polkit-1/rules.d/91-vexor-victorialogs.rules.
    cmd = ["systemctl", "restart", "vexor-victorialogs"]
    try:
        r = subprocess.run(cmd, check=False, timeout=20, capture_output=True)
        if r.returncode != 0:
            stderr_txt = (r.stderr or b"").decode(errors="replace").strip()
            log.warning("vexor-victorialogs restart failed rc=%s stderr=%s",
                        r.returncode, stderr_txt[:300])
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "restart_failed",
                    "message": ("Settings were saved but vexor-victorialogs failed to restart. "
                                "The configured retention is not yet active on the running daemon."),
                    "rc": r.returncode,
                    "stderr": stderr_txt[:500],
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("victorialogs restart failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "restart_exception",
                "message": "Settings were saved but the restart command could not be executed.",
                "exception": f"{type(e).__name__}: {e}",
            },
        )
    return _current_settings()


# ---------------------------------------------------------------------------
# /storage
# ---------------------------------------------------------------------------
def _stat_storage() -> tuple[int, int]:
    used = 0
    try:
        for root, _dirs, files in os.walk(STORAGE_DIR):
            for f in files:
                try:
                    used += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    try:
        free = shutil.disk_usage(str(STORAGE_DIR)).free
    except OSError:
        free = 0
    return used, free


def _parse_metric(text: str, name: str) -> Optional[float]:
    rx = re.compile(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)\s*$", re.M)
    m = rx.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _oldest_log_ts() -> Optional[str]:
    # Cheap heuristic: oldest file mtime under storage dir.
    try:
        oldest: Optional[float] = None
        for root, _dirs, files in os.walk(STORAGE_DIR):
            for f in files:
                try:
                    m = os.path.getmtime(os.path.join(root, f))
                    if oldest is None or m < oldest:
                        oldest = m
                except OSError:
                    pass
        if oldest is None:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat()
    except OSError:
        return None


@router.get("/storage")
async def storage(_=Depends(require_viewer)) -> dict:
    import asyncio as _asyncio
    settings = _current_settings()
    metrics = await _asyncio.to_thread(_client.metrics_text)
    metric_used = _parse_metric(metrics, "vlstorage_data_size_bytes")
    used_disk, free = await _asyncio.to_thread(_stat_storage)
    oldest = await _asyncio.to_thread(_oldest_log_ts)
    used = int(metric_used) if metric_used is not None else used_disk
    return {
        "used_bytes": used,
        "used_bytes_disk": used_disk,
        "free_bytes": free,
        "oldest_log_ts": oldest,
        "retention_days": settings.retention_days,
        "disk_mode": settings.disk_mode,
        "disk_bytes": settings.disk_bytes,
        "disk_percent": settings.disk_percent,
        "storage_path": str(STORAGE_DIR),
        "partitions": [{"path": str(STORAGE_DIR), "used_bytes": used_disk, "free_bytes": free}],
    }

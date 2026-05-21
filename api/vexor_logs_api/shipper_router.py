"""POST /api/v1/logs/deploy-shipper — remote install of vector / fluent-bit.

This re-uses on-disk SSH host_credentials rows already managed by
vexor-api (see ``app/services/credentials_router``). Output of the remote
install script is captured and returned in-line; for a fancy streaming
UI we'd hook this up to websockets later.
"""
from __future__ import annotations
import asyncio
import logging
import os
import shlex
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from app.services.auth import require_admin  # type: ignore
except Exception:
    def require_admin(): return None  # type: ignore

try:
    from app.routers.credentials_router import _key_path, _pw_path  # type: ignore
except Exception:
    def _key_path(cid): return None  # type: ignore
    def _pw_path(cid): return None   # type: ignore


log = logging.getLogger("vexor.logs.shipper")
router = APIRouter(prefix="/api/v1/logs", tags=["logs-shipper"])

INSTALL_SCRIPT_DIR = Path("/opt/vexor/api/plugins/logs/install-scripts")


class DeployIn(BaseModel):
    host: str
    transport: Literal["ssh", "winrm"] = "ssh"
    credentials_id: Optional[int] = None
    username: Optional[str] = None
    port: int = 22
    agent: Literal["vector", "fluentbit"] = "vector"
    vexor_url: Optional[str] = None
    token: str = ""
    logs: list[str] = Field(default_factory=lambda: ["/var/log"])


@router.get("/deploy-shipper/script")
def get_script(agent: str = "vector", target: str = "linux") -> dict:
    """Return the install script contents so the UI can show / download it."""
    if target == "windows":
        candidates = [INSTALL_SCRIPT_DIR / "install-windows-agent.ps1",
                      Path("/opt/vexor/api/plugins/logs/scripts/install-windows-agent.ps1")]
    else:
        candidates = [INSTALL_SCRIPT_DIR / "install-linux-agent.sh",
                      Path("/opt/vexor/api/plugins/logs/scripts/install-linux-agent.sh")]
    for p in candidates:
        if p.exists():
            return {"path": str(p), "content": p.read_text()}
    raise HTTPException(404, "install script not packaged")


def _public_url() -> str:
    return os.environ.get("VEXOR_PUBLIC_URL", f"https://{socket.gethostname()}")


def _ssh_run(host: str, port: int, user: str, key_path: Optional[str],
             remote_cmd: str, stdin: Optional[bytes] = None,
             timeout: int = 300) -> tuple[int, str, str]:
    args = ["ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            "-p", str(port)]
    if key_path:
        args += ["-i", key_path]
    args += [f"{user}@{host}", remote_cmd]
    try:
        p = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


@router.post("/deploy-shipper")
def deploy(body: DeployIn, _=Depends(require_admin)) -> dict:
    if body.transport == "winrm":
        # WinRM isn't built into the Python stdlib; we expose the URL/token
        # combo so the admin can paste a one-liner into a Windows shell.
        target = "windows"
        url = body.vexor_url or _public_url()
        # Try to read the packaged script for inline preview / future
        # remote execution via pywinrm (not yet wired).
        try:
            content = get_script("vector", "windows")["content"]
        except HTTPException:
            content = ""
        return {
            "ok": False,
            "stage": "manual-required",
            "message": (
                "WinRM transport not yet implemented server-side. "
                "Run the following PowerShell on the Windows host:"
            ),
            "command": (
                f"iwr {url}/api/v1/logs/install-scripts/install-windows-agent.ps1 "
                f"-OutFile $env:TEMP\\vexor-vector.ps1; "
                f"powershell -ExecutionPolicy Bypass -File $env:TEMP\\vexor-vector.ps1 "
                f"-VexorUrl {url} -Token {shlex.quote(body.token) if body.token else '<token>'} "
                f"-Agent {body.agent}"
            ),
            "script_preview": content[:2000],
        }
    # SSH path -------------------------------------------------------------
    user = body.username or "root"
    key = None
    if body.credentials_id:
        try:
            kp = _key_path(body.credentials_id)
            if kp and Path(kp).exists():
                key = kp
        except Exception:
            key = None
    # Locate the install script we ship in this RPM
    try:
        scr = get_script("vector", "linux")["content"]
    except HTTPException:
        raise HTTPException(500, "install-linux-agent.sh not packaged")
    url = body.vexor_url or _public_url()
    args = [f"--vexor-url {shlex.quote(url)}",
            f"--token {shlex.quote(body.token)}",
            f"--agent {shlex.quote(body.agent)}"]
    for l in body.logs:
        args.append(f"--log {shlex.quote(l)}")
    # Pipe the script over stdin to bash so we don't need to scp a file.
    remote = f"sudo bash -s -- {' '.join(args)}"
    rc, out, err = _ssh_run(body.host, body.port, user, key,
                            remote, stdin=scr.encode("utf-8"))
    return {
        "ok": rc == 0,
        "rc": rc,
        "stdout": out[-8000:],
        "stderr": err[-8000:],
        "host": body.host,
        "agent": body.agent,
    }

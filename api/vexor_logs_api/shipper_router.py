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
from app.services import jobs
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
    from app.services.auth import require_viewer  # type: ignore
except Exception:
    def require_viewer(): return None  # type: ignore

try:
    from app.routers.credentials_router import _key_path, _pw_path  # type: ignore
except Exception:
    def _key_path(cid): return None  # type: ignore
    def _pw_path(cid): return None   # type: ignore


log = logging.getLogger("vexor.logs.shipper")
def _db_query_one(sql: str, params: dict):
    import os, pymysql
    from urllib.parse import urlparse
    url = os.environ.get("VEXOR_DB_URL", "")
    if not url:
        # try reading /etc/vexor/db.env
        try:
            for ln in open("/etc/vexor/db.env"):
                if ln.startswith("VEXOR_DB_URL="):
                    url = ln.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
    if not url:
        return None
    u = urlparse(url.replace("mysql+pymysql", "mysql").replace("mysql+asyncmy", "mysql"))
    conn = pymysql.connect(
        host=u.hostname or "127.0.0.1",
        port=u.port or 3306,
        user=u.username or "vexor",
        password=u.password or "",
        database=(u.path or "/vexor").lstrip("/"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()

router = APIRouter(prefix="/api/v1/logs", tags=["logs-shipper"])

INSTALL_SCRIPT_DIR = Path("/opt/vexor/api/plugins/logs/install-scripts")

_INGEST_ENV = Path("/etc/vexor/logs-ingest.env")


def _ingest_token() -> str:
    """Return the log ingest token (matches the nginx `Bearer <token>` map).

    Shippers must send `Authorization: Bearer <token>` to /api/v1/logs/push or
    nginx returns 401. The token lives in VEXOR_LOGS_INGEST_TOKEN (env) or the
    root:vexor 0640 file /etc/vexor/logs-ingest.env, readable by the api user.
    """
    tok = os.environ.get("VEXOR_LOGS_INGEST_TOKEN", "").strip()
    if tok:
        return tok
    try:
        for ln in _INGEST_ENV.read_text().splitlines():
            ln = ln.strip()
            if ln.startswith("VEXOR_LOGS_INGEST_TOKEN="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


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


@router.get("/deploy-shipper/script", dependencies=[Depends(require_viewer)])
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


def _ssh_argv(host: str, port: int, user: str,
              key_path: Optional[str] = None,
              password: Optional[str] = None,
              remote_cmd: str = "") -> tuple[list, Optional[dict]]:
    """Build the ssh/sshpass argv + env (shared by _ssh_run and streamed jobs)."""
    base = ["-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=/var/lib/vexor/known_hosts",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=15",
            "-p", str(port)]
    if key_path:
        args = ["ssh", "-o", "BatchMode=yes", "-i", key_path] + base + [f"{user}@{host}", remote_cmd]
        env = None
    elif password:
        args = ["sshpass", "-e", "ssh",
                "-o", "PreferredAuthentications=password",
                "-o", "PubkeyAuthentication=no",
                "-o", "NumberOfPasswordPrompts=1"] + base + [f"{user}@{host}", remote_cmd]
        env = os.environ.copy()
        env["SSHPASS"] = password
    else:
        args = ["ssh", "-o", "BatchMode=yes"] + base + [f"{user}@{host}", remote_cmd]
        env = None
    return args, env


def _ssh_run(host: str, port: int, user: str,
             key_path: Optional[str] = None,
             password: Optional[str] = None,
             remote_cmd: str = "",
             stdin: Optional[bytes] = None,
             timeout: int = 300) -> tuple[int, str, str]:
    base = ["-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=/var/lib/vexor/known_hosts",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=15",
            "-p", str(port)]
    if key_path:
        args = ["ssh", "-o", "BatchMode=yes", "-i", key_path] + base + [f"{user}@{host}", remote_cmd]
        env = None
    elif password:
        # sshpass: pipes password to ssh; do NOT use BatchMode.
        args = ["sshpass", "-e", "ssh",
                "-o", "PreferredAuthentications=password",
                "-o", "PubkeyAuthentication=no",
                "-o", "NumberOfPasswordPrompts=1"] + base + [f"{user}@{host}", remote_cmd]
        env = os.environ.copy()
        env["SSHPASS"] = password
    else:
        args = ["ssh", "-o", "BatchMode=yes"] + base + [f"{user}@{host}", remote_cmd]
        env = None
    try:
        p = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout, env=env)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except FileNotFoundError as e:
        return 127, "", f"binary missing: {e}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


@router.post("/deploy-shipper")
async def deploy(body: DeployIn, _=Depends(require_admin)) -> dict:
    _decrypted_key_path = None
    if body.transport == "winrm":
        target = "windows"
        url = body.vexor_url or _public_url()
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
                f'curl.exe -k -o "$env:TEMP\\vexor-vector.ps1" '
                f"{url}/api/v1/logs/install-scripts/install-windows-agent.ps1; "
                f'powershell -ExecutionPolicy Bypass -File "$env:TEMP\\vexor-vector.ps1" '
                f'-VexorUrl {url} -Token "{body.token or _ingest_token() or "<TOKEN>"}" '
                f"-Agent {body.agent}"
            ),
            "script_preview": content[:2000],
        }
    # SSH path -----------------------------------------------------------
    user = body.username or "root"
    key: Optional[str] = None
    password: Optional[str] = None
    target_host = body.host

    # Look up host in hosts table to translate name -> address
    try:
        row = _db_query_one(
            "SELECT address, credential_id FROM hosts WHERE name=%(n)s",
            {"n": body.host},
        )
        if row:
            if row.get("address"):
                target_host = row["address"]
            if not body.credentials_id and row.get("credential_id"):
                body.credentials_id = row["credential_id"]
    except Exception as e:
        log.warning("host lookup failed: %s", e)

    # Look up credential username/password/key
    if body.credentials_id:
        try:
            cred = _db_query_one(
                "SELECT username, password_enc, private_key_enc "
                "FROM host_credentials WHERE id=%(i)s",
                {"i": body.credentials_id},
            )
            if cred:
                if cred.get("username") and not body.username:
                    user = cred["username"]
                try:
                    from app.services.crypto import decrypt  # type: ignore
                    pw = decrypt(cred.get("password_enc")) if cred.get("password_enc") else None
                    pk = decrypt(cred.get("private_key_enc")) if cred.get("private_key_enc") else None
                except Exception:
                    pw, pk = None, None
                if not pw:
                    pp = _pw_path(body.credentials_id)
                    if pp and Path(pp).exists():
                        pw = Path(pp).read_text().strip()
                if not pk:
                    kp = _key_path(body.credentials_id)
                    if kp and Path(kp).exists():
                        key = kp
                else:
                    import tempfile
                    tf = tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".key")
                    try:
                        tf.write(pk); tf.close()
                        os.chmod(tf.name, 0o600)
                    except Exception:
                        try: os.unlink(tf.name)
                        except Exception: pass
                        raise
                    key = tf.name
                    _decrypted_key_path = key  # cleaned up in finally below
                if pw and not key:
                    password = pw
        except Exception as e:
            log.warning("credential lookup failed: %s", e)

    if not key and not password:
        if _decrypted_key_path:
            try: os.unlink(_decrypted_key_path)
            except Exception: pass
        raise HTTPException(400, "no SSH credential available (need key or password)")

    # ---- key is now on disk; wrap remaining work in try/finally to ensure cleanup
    try:
        try:
            scr = get_script("vector", "linux")["content"]
        except HTTPException:
            raise HTTPException(500, "install-linux-agent.sh not packaged")
        url = body.vexor_url or _public_url()
        args = [f"--vexor-url {shlex.quote(url)}",
            f"--token {shlex.quote(body.token)}",
            f"--agent {shlex.quote(body.agent)}",
            f"--host-name {shlex.quote(body.host)}"]
        for l in body.logs:
            args.append(f"--log {shlex.quote(l)}")
        # If using password auth user is non-root, prefix sudo with -S to read pw from stdin
        if password and user != "root":
            remote = f"sudo -S bash -s -- {" ".join(args)}"
            stdin_payload = (password + "\n").encode() + scr.encode("utf-8")
        else:
            remote = f"sudo bash -s -- {" ".join(args)}" if user != "root" else f"bash -s -- {" ".join(args)}"
            stdin_payload = scr.encode("utf-8")

        # Run the install as a streamed background job so the UI shows live
        # progress instead of an endless spinner. Ownership of the temp key is
        # handed to the job (cleaned up when it finishes).
        args, env = _ssh_argv(target_host, body.port, user,
                              key_path=key, password=password, remote_cmd=remote)
        cleanup = [_decrypted_key_path] if _decrypted_key_path else None
        job_id = jobs.start_local(
            [(f"Installing log shipper on {target_host}", args)],
            env=env, stdin=stdin_payload, cleanup_paths=cleanup,
        )
        _decrypted_key_path = None  # ownership transferred to the streamed job
        return {
            "ok": True,
            "job_id": job_id,
            "host": target_host,
            "user": user,
            "agent": body.agent,
        }
    finally:
        if _decrypted_key_path:
            try: os.unlink(_decrypted_key_path)
            except Exception: pass

from fastapi.responses import PlainTextResponse, Response


@router.get("/ingest-token", dependencies=[Depends(require_admin)])
def get_ingest_token() -> dict:
    """Admin-only: reveal the current log ingest token so it can be shown in the
    GUI and injected into shipper install commands. Shippers authenticate to
    /api/v1/logs/push with `Authorization: Bearer <token>`."""
    tok = _ingest_token()
    return {"configured": bool(tok), "token": tok, "scheme": "Bearer"}

_ALLOWED_SCRIPTS = {
    "install-linux-agent.sh":              ("text/x-shellscript", "linux"),
    "install-linux-agent-interactive.sh":  ("text/x-shellscript", "linux"),
    "install-windows-agent.ps1":           ("text/x-powershell",  "windows"),
    "install-windows-agent-interactive.ps1": ("text/x-powershell","windows"),
}

@router.get("/install-scripts", dependencies=[Depends(require_viewer)])
def list_install_scripts() -> dict:
    out = []
    for name, (ctype, os_) in _ALLOWED_SCRIPTS.items():
        p = INSTALL_SCRIPT_DIR / name
        out.append({
            "name": name, "os": os_, "content_type": ctype,
            "available": p.exists(),
            "size": p.stat().st_size if p.exists() else 0,
            "interactive": "interactive" in name,
            "url": f"/api/v1/logs/install-scripts/{name}",
        })
    return {"scripts": out}

@router.get("/install-scripts/{name}", dependencies=[Depends(require_viewer)])
def serve_install_script(name: str):
    meta = _ALLOWED_SCRIPTS.get(name)
    if not meta:
        raise HTTPException(404, "unknown install script")
    p = INSTALL_SCRIPT_DIR / name
    if not p.exists():
        raise HTTPException(404, "install script not packaged")
    return Response(
        content=p.read_bytes(),
        media_type=meta[0],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


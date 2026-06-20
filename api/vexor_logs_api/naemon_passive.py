"""Naemon passive service helpers for log-alert host bindings.

When a log alert rule has ``host_binding`` set we want it to appear as a
passive service on that Naemon host so it shows up in dashboards / BSM /
notifications. This module:

  * writes / removes per-rule service stanzas in
    /etc/naemon/vexor/services/log_alerts.cfg and asks naemon to reload
  * submits PROCESS_SERVICE_CHECK_RESULT lines to the naemon command pipe
"""
from __future__ import annotations
import os
import re
import subprocess
import time
from pathlib import Path

LOG_SERVICES_FILE = Path("/etc/naemon/vexor/services/log_alerts.cfg")
NAEMON_CMD_FILE = "/var/lib/naemon/naemon.cmd"

# Marker tags so we can rewrite individual rule blocks cleanly.
_BEGIN = "# vexor-log-alert begin:"
_END = "# vexor-log-alert end:"

_SVC_TEMPLATE = """{begin} {key}
define service {{
    use                       generic-service
    host_name                 {host}
    service_description       {svc}
    display_name              Log alert: {name}
    check_command             check_dummy!0!log alert OK
    active_checks_enabled     0
    passive_checks_enabled    1
    check_freshness           0
    notification_options      w,c,r
    notes                     Vexor Logs alert rule
}}
{end} {key}
"""


_HOST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,253}$")


class InvalidHostName(ValueError):
    pass


class UnknownHost(ValueError):
    """host_binding refers to a host that is not configured in Naemon."""
    pass


class NaemonReloadFailed(RuntimeError):
    """Naemon rejected the new config; the broken stanza has been rolled back."""
    pass


def _validate_host_name(host: str) -> str:
    """Reject anything that could escape the Naemon config template."""
    if not host or not _HOST_NAME_RE.match(host):
        raise InvalidHostName(f"invalid host name: {host!r}")
    return host


def slugify_rule_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").lower()
    return s[:60] or "rule"


def service_name(rule_slug: str) -> str:
    return f"vexor_logs_{rule_slug}"


def _key(host: str, slug: str) -> str:
    return f"{host}::{slug}"


def _read_blocks() -> dict[str, str]:
    if not LOG_SERVICES_FILE.exists():
        return {}
    text = LOG_SERVICES_FILE.read_text()
    blocks: dict[str, str] = {}
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(_BEGIN):
            key = line[len(_BEGIN):].strip()
            buf = [line]
            i += 1
            while i < len(lines):
                buf.append(lines[i])
                if lines[i].startswith(_END):
                    i += 1
                    break
                i += 1
            blocks[key] = "".join(buf)
        else:
            i += 1
    return blocks


_LOCK_FILE = Path("/run/vexor/vexor-logs-services.lock")


def _lock():
    import fcntl
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fp = open(_LOCK_FILE, "w")
    fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
    return fp


def _write_blocks(blocks: dict[str, str]) -> None:
    LOG_SERVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "# Managed by vexor-logs — do not edit by hand.\n\n"
    body += "\n".join(blocks[k] for k in sorted(blocks))
    lock_fp = _lock()
    try:
        tmp = LOG_SERVICES_FILE.with_suffix(LOG_SERVICES_FILE.suffix + ".tmp")
        tmp.write_text(body)
        try:
            os.chmod(tmp, 0o644)
        except Exception:
            pass
        os.replace(tmp, LOG_SERVICES_FILE)
    finally:
        try: lock_fp.close()
        except Exception: pass


def _reload_naemon() -> tuple[bool, str]:
    """Reload naemon. Returns (ok, stderr). Never raises."""
    # Verify the file is syntactically valid first; that gives a much clearer
    # stderr than the post-reload "control process exited" from systemd.
    try:
        r = subprocess.run(["naemon", "-v", "/etc/naemon/naemon.cfg"],
                           check=False, timeout=20, capture_output=True)
        if r.returncode != 0:
            err = (r.stderr or b"").decode(errors="replace")
            out = (r.stdout or b"").decode(errors="replace")
            return False, (err + out).strip()[:1000]
    except FileNotFoundError:
        # naemon binary not on PATH (dev env) - skip verification, try reload anyway
        pass
    except Exception as e:
        return False, f"naemon -v failed: {e}"
    try:
        r = subprocess.run(["systemctl", "reload", "naemon"],
                           check=False, timeout=15, capture_output=True)
        if r.returncode != 0:
            err = (r.stderr or b"").decode(errors="replace")
            return False, err.strip()[:1000] or f"systemctl reload rc={r.returncode}"
        return True, ""
    except Exception as e:
        return False, str(e)


def host_exists(host: str) -> bool:
    """Check if a host is configured in Naemon by scanning generated cfgs.

    We look at /var/cache/naemon/objects.cache (compiled object index) which
    is regenerated on each successful reload, then fall back to any
    host_name definition under /etc/naemon/vexor/hosts/.
    """
    try:
        # Fast path: objects.cache is plain text and lists every parsed host.
        cache = Path("/var/cache/naemon/objects.cache")
        if cache.exists():
            # log-14: exact-line match to avoid substring false positives
            tab_form = "host_name\t" + host
            sp_form = "host_name " + host
            with cache.open() as fh:
                for line in fh:
                    stripped = line.rstrip("\n\r").lstrip("\t ")
                    if stripped == tab_form or stripped == sp_form:
                        return True
        # Fallback for fresh installs: scan /etc/naemon/vexor/hosts/
        hosts_dir = Path("/etc/naemon/vexor/hosts")
        if hosts_dir.exists():
            pattern = re.compile(rf"^\s*host_name\s+{re.escape(host)}\s*$", re.M)
            for cfg in hosts_dir.glob("*.cfg"):
                try:
                    if pattern.search(cfg.read_text()):
                        return True
                except OSError:
                    continue
    except Exception:
        return True  # be permissive on errors; rely on _reload_naemon to catch real breakage
    return False


def ensure_log_service(host: str, rule_slug: str, rule_name: str) -> None:
    """Create or refresh the passive service stanza and reload naemon.

    Raises:
        InvalidHostName - host_binding fails strict regex
        UnknownHost     - host_binding is not a known Naemon host
        NaemonReloadFailed - new config rejected; previous stanza restored
    """
    host = _validate_host_name(host)
    if not host_exists(host):
        raise UnknownHost(host)
    key = _key(host, rule_slug)
    svc = service_name(rule_slug)
    block = _SVC_TEMPLATE.format(begin=_BEGIN, end=_END, key=key,
                                 host=host, svc=svc, name=rule_name)
    blocks = _read_blocks()
    existing = blocks.get(key)
    if existing == block:
        return
    # log-15: detect slug collision with a different rule_name in the same host.
    # If existing stanza references a different display name, append numeric suffix.
    if existing and ("name " + rule_name) not in existing:
        base = rule_slug
        n = 2
        while _key(host, base + "_" + str(n)) in blocks:
            n += 1
        rule_slug = base + "_" + str(n)
        key = _key(host, rule_slug)
        svc = service_name(rule_slug)
        block = _SVC_TEMPLATE.format(begin=_BEGIN, end=_END, key=key,
                                     host=host, svc=svc, name=rule_name)
        existing = blocks.get(key)
    blocks[key] = block
    _write_blocks(blocks)
    ok, err = _reload_naemon()
    if not ok:
        # Roll back the stanza so naemon can be brought back up.
        if existing is None:
            blocks.pop(key, None)
        else:
            blocks[key] = existing
        _write_blocks(blocks)
        _reload_naemon()
        raise NaemonReloadFailed(err)


def remove_log_service(host: str, rule_slug: str) -> None:
    if not host:
        return
    try:
        host = _validate_host_name(host)
    except InvalidHostName:
        return
    key = _key(host, rule_slug)
    blocks = _read_blocks()
    if key in blocks:
        existing = blocks.pop(key)
        _write_blocks(blocks)
        ok, err = _reload_naemon()
        if not ok:
            # Restore so we don't leave naemon broken; let caller see the error.
            blocks[key] = existing
            _write_blocks(blocks)
            _reload_naemon()
            raise NaemonReloadFailed(err)


def _sanitize(s: str) -> str:
    return (s or "").replace("\r", " ").replace("\n", " ").replace("\x00", "").replace(";", ",")


def submit_passive_result(host: str, svc: str, return_code: int, output: str) -> None:
    """Submit a PROCESS_SERVICE_CHECK_RESULT to the naemon external command pipe."""
    if not host or not svc:
        return
    host_s = _sanitize(host)
    svc_s = _sanitize(svc)
    out_s = _sanitize(output)
    line = (f"[{int(time.time())}] PROCESS_SERVICE_CHECK_RESULT;"
            f"{host_s};{svc_s};{int(return_code)};{out_s}\n")
    try:
        fd = os.open(NAEMON_CMD_FILE, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return  # pipe may not exist in dev/test
    try:
        os.write(fd, line.encode("utf-8", "replace"))
    finally:
        os.close(fd)

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


def _write_blocks(blocks: dict[str, str]) -> None:
    LOG_SERVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = "# Managed by vexor-logs — do not edit by hand.\n\n"
    body += "\n".join(blocks[k] for k in sorted(blocks))
    LOG_SERVICES_FILE.write_text(body)
    try:
        os.chmod(LOG_SERVICES_FILE, 0o644)
    except Exception:
        pass


def _reload_naemon() -> None:
    # Prefer systemctl reload; ignore failures (e.g. dev/test environments).
    try:
        subprocess.run(["systemctl", "reload", "naemon"], check=False,
                       timeout=15, capture_output=True)
    except Exception:
        pass


def ensure_log_service(host: str, rule_slug: str, rule_name: str) -> None:
    """Create or refresh the passive service stanza and reload naemon."""
    if not host:
        return
    key = _key(host, rule_slug)
    svc = service_name(rule_slug)
    block = _SVC_TEMPLATE.format(begin=_BEGIN, end=_END, key=key,
                                 host=host, svc=svc, name=rule_name)
    blocks = _read_blocks()
    existing = blocks.get(key)
    if existing == block:
        return
    blocks[key] = block
    _write_blocks(blocks)
    _reload_naemon()


def remove_log_service(host: str, rule_slug: str) -> None:
    if not host:
        return
    key = _key(host, rule_slug)
    blocks = _read_blocks()
    if key in blocks:
        blocks.pop(key)
        _write_blocks(blocks)
        _reload_naemon()


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

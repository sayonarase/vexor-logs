"""Log anomaly detection engine for the Vexor logs plugin.

Three detector kinds, evaluated by the existing vexor-log-alerts-evaluator
daemon (no new systemd unit):

  * ``baseline`` - statistical volume/rate anomaly. Compares the current
    window against a rolling baseline of equal-length buckets using a robust
    z-score (median + MAD, falling back to mean + stddev). Fires on
    ``spike`` / ``drop`` / ``both``.
  * ``novelty``  - log-template novelty. Masks the variable parts of each
    message (numbers, IPs, hex, UUIDs, quoted strings, timestamps) into a
    stable template signature and fires when a template appears that was
    never seen before (after an initial silent learning pass).
  * ``watch``    - a natural-language concern ("look for signs of intrusion
    or disk failure") evaluated on a sample of logs by the shared LLM
    provider. Opt-in and throttled because it costs tokens.

A firing detector is recorded in ``log_anomaly_events`` and, when the monitor
has ``host_binding`` set, submitted to Naemon as a passive result so it shows
up on the monitoring console (and therefore SLA reports, BSM, notifications).

The engine adds no third-party Python dependencies: the template miner is a
self-contained tokeniser, and statistics are computed with the stdlib.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select

from . import _client
from .models import LogAnomalyMonitor, LogAnomalyEvent, LogAnomalyTemplate
from .naemon_passive import (
    slugify_rule_name, service_name, submit_passive_result,
    host_exists, _validate_host_name, InvalidHostName,
)

log = logging.getLogger("vexor.logs.anomaly")

_SEV_TO_RC = {"info": 0, "ok": 0, "warning": 1, "critical": 2, "unknown": 3}

# Cap how many rows we pull for novelty / watch to keep memory + tokens sane.
_NOVELTY_LIMIT = int(os.environ.get("VEXOR_ANOMALY_NOVELTY_LIMIT", "3000"))
_WATCH_LIMIT = int(os.environ.get("VEXOR_ANOMALY_WATCH_LIMIT", "200"))
_WATCH_MAX_CHARS = 12000


# --------------------------------------------------------------------------
# Template mining (Drain-inspired, dependency-free)
# --------------------------------------------------------------------------
_MASKS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"), "<MAC>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b"), "<IPV6>"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HEX>"),
    (re.compile(r'"[^"]*"'), "<STR>"),
    (re.compile(r"'[^']*'"), "<STR>"),
    (re.compile(r"\b\d+\b"), "<NUM>"),
]


def templatize(msg: str) -> tuple[str, str]:
    """Return (signature, template) for a raw log message.

    The template is the message with variable tokens masked; the signature is
    a short stable hash of that template used as the dedup key.
    """
    t = (msg or "").strip()
    for pat, repl in _MASKS:
        t = pat.sub(repl, t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 400:
        t = t[:400]
    sig = hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()[:16]
    return sig, t


# --------------------------------------------------------------------------
# Detection result
# --------------------------------------------------------------------------
@dataclass
class Detection:
    fired: bool
    rc: int
    output: str
    score: float = 0.0
    observed: float = 0.0
    baseline_mean: float = 0.0
    baseline_std: float = 0.0
    template: Optional[str] = None
    sample: str = ""
    details: dict = field(default_factory=dict)


def _msg_of(row: dict) -> str:
    if not isinstance(row, dict):
        return str(row)
    return str(row.get("_msg") or row.get("message") or row.get("msg") or row.get("_raw") or "")


def _fmt_row(row: dict) -> str:
    t = row.get("_time") or row.get("time") or ""
    host = row.get("host") or row.get("hostname") or ""
    msg = _msg_of(row)
    prefix = " ".join(p for p in (str(t)[:19], str(host)) if p)
    line = f"{prefix} | {msg}" if prefix else msg
    return line[:500]


# --------------------------------------------------------------------------
# Baseline (statistical) detector
# --------------------------------------------------------------------------
def _robust_stats(values: list[float]) -> tuple[float, float]:
    """Return (center, scale) using median + MAD, falling back to mean+std."""
    if not values:
        return 0.0, 0.0
    med = statistics.median(values)
    devs = [abs(v - med) for v in values]
    mad = statistics.median(devs)
    scale = 1.4826 * mad
    if scale <= 1e-9:
        try:
            scale = statistics.pstdev(values)
        except statistics.StatisticsError:
            scale = 0.0
    return med, scale


def run_baseline(mon: "LogAnomalyMonitor") -> Detection:
    """Volume/rate anomaly using the VictoriaLogs hits histogram."""
    window = max(int(mon.window_sec or 300), 30)
    baseline = max(int(mon.baseline_sec or 86400), window * 4)
    step = f"{window}s"
    now = datetime.now(timezone.utc)
    start = (now - timedelta(seconds=baseline)).isoformat()
    end = now.isoformat()

    env = _client.hits(mon.query or "*", start=start, end=end, step=step)
    hits = (env or {}).get("hits") or []
    values: list[float] = []
    if hits and isinstance(hits[0], dict):
        values = [float(v) for v in (hits[0].get("values") or [])]
    # The final bucket covers the still-in-progress window and always reads
    # low, so drop it and treat the last COMPLETE bucket as "current". This
    # adds up to window_sec of lag but avoids constant false "drop" alerts.
    if len(values) < 5:
        return Detection(False, 0,
                          f"OK: not enough history yet ({len(values)} buckets) to baseline")
    values = values[:-1]
    current = values[-1]
    hist = values[:-1]
    center, scale = _robust_stats(hist)
    direction = (mon.direction or "both").lower()
    sensitivity = float(mon.sensitivity or 3.0)
    min_baseline = float(mon.min_baseline or 0)

    if scale <= 1e-9:
        score = 0.0 if current == center else (math.inf if current > center else -math.inf)
    else:
        score = (current - center) / scale

    sev = (mon.severity or "warning").lower()
    rc = _SEV_TO_RC.get(sev, 1)

    fired = False
    kind = "normal"
    if direction in ("spike", "both") and score >= sensitivity and current >= max(min_baseline, center):
        fired, kind = True, "spike"
    elif direction in ("drop", "both") and (
        (score <= -sensitivity) or (current == 0 and center >= max(min_baseline, 1))
    ) and center >= max(min_baseline, 1):
        fired, kind = True, "drop"

    disp = "inf" if math.isinf(score) else f"{score:.1f}"
    if fired:
        output = (
            f"{sev.upper()}: {kind} detected - current {current:.0f} vs "
            f"baseline median {center:.1f} (z={disp}, threshold {sensitivity:g})"
        )
    else:
        rc = 0
        output = (
            f"OK: {current:.0f} in last {window}s, baseline median {center:.1f} "
            f"(z={disp})"
        )
    return Detection(fired, rc, output, score=(0.0 if math.isinf(score) else score),
                     observed=current, baseline_mean=center, baseline_std=scale,
                     details={"kind": kind, "buckets": len(values)})


# --------------------------------------------------------------------------
# Novelty (template) detector - async, needs DB
# --------------------------------------------------------------------------
async def run_novelty(mon: "LogAnomalyMonitor", db) -> Detection:
    window = max(int(mon.window_sec or 300), 30)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(seconds=window)).isoformat()

    def _q():
        return _client.query(mon.query or "*", limit=_NOVELTY_LIMIT, start=start)
    rows = await asyncio.to_thread(_q)

    seen: dict[str, str] = {}
    examples: dict[str, str] = {}
    for row in rows:
        msg = _msg_of(row)
        if not msg:
            continue
        sig, tmpl = templatize(msg)
        seen[sig] = tmpl
        if sig not in examples:
            examples[sig] = _fmt_row(row) if isinstance(row, dict) else msg

    # Load previously-known signatures for this monitor.
    known_rows = (await db.execute(
        select(LogAnomalyTemplate).where(LogAnomalyTemplate.monitor_id == mon.id)
    )).scalars().all()
    known = {k.signature: k for k in known_rows}
    learning = len(known) == 0

    novel: list[str] = []
    for sig, tmpl in seen.items():
        rec = known.get(sig)
        if rec is None:
            db.add(LogAnomalyTemplate(
                monitor_id=mon.id, signature=sig, template=tmpl, hits=1))
            if not learning:
                novel.append(sig)
        else:
            rec.hits = (rec.hits or 0) + 1
            rec.last_seen = now

    sev = (mon.severity or "warning").lower()
    if learning:
        return Detection(False, 0,
                         f"OK: learning baseline - captured {len(seen)} template(s)",
                         details={"learning": True, "templates": len(seen)})
    if not novel:
        return Detection(False, 0,
                         f"OK: no new log templates ({len(seen)} known template(s) seen)",
                         details={"templates": len(seen)})

    rc = _SEV_TO_RC.get(sev, 1)
    sample = "\n".join(examples[s] for s in novel[:5])
    output = (
        f"{sev.upper()}: {len(novel)} new log template(s) never seen before "
        f"(e.g. {seen[novel[0]][:120]})"
    )
    return Detection(True, rc, output, score=float(len(novel)),
                     observed=float(len(novel)), template=seen[novel[0]],
                     sample=sample, details={"novel": len(novel)})


# --------------------------------------------------------------------------
# Watch (LLM) detector - async, needs DB + LLM
# --------------------------------------------------------------------------
_WATCH_SYSTEM = (
    "You are a security/operations analyst watching a stream of log lines for "
    "a specific concern described by the operator. Decide whether the supplied "
    "logs show evidence of that concern. Reply with ONLY a compact JSON object "
    '{"severity":"ok|warning|critical","reason":"<one short sentence>"}. '
    "Use 'ok' when there is no evidence. Never invent log content; base the "
    "verdict only on the supplied lines."
)


async def run_watch(mon: "LogAnomalyMonitor", db) -> Detection:
    try:
        from app.routers.llm_router import _generate  # type: ignore
    except Exception:
        return Detection(False, 3, "UNKNOWN: LLM provider unavailable on this build")

    window = max(int(mon.window_sec or 900), 60)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(seconds=window)).isoformat()

    def _q():
        return _client.query(mon.query or "*", limit=_WATCH_LIMIT, start=start)
    rows = await asyncio.to_thread(_q)
    if not rows:
        return Detection(False, 0, "OK: no logs in window to evaluate")

    lines: list[str] = []
    total = 0
    for row in rows:
        s = _fmt_row(row) if isinstance(row, dict) else str(row)
        total += len(s) + 1
        if total > _WATCH_MAX_CHARS:
            break
        lines.append(s)
    sample = "\n".join(lines)
    concern = (mon.nl_question or "anything unusual or suspicious").strip()
    prompt = (
        f"Operator's concern: {concern}\n\n"
        f"--- BEGIN LOGS ({len(lines)} lines, newest first) ---\n"
        f"{sample}\n--- END LOGS ---"
    )
    try:
        raw = await _generate(db, prompt, _WATCH_SYSTEM, None, 300)
    except Exception as exc:
        return Detection(False, 3, f"UNKNOWN: LLM evaluation failed: {exc}")

    verdict, reason = _parse_watch(raw)
    rc = _SEV_TO_RC.get(verdict, 3)
    fired = rc >= 1
    output = f"{verdict.upper()}: {reason}" if reason else f"{verdict.upper()}"
    return Detection(fired, rc, output, score=float(rc),
                     sample=sample[:2000], details={"verdict": verdict})


def _parse_watch(raw: str) -> tuple[str, str]:
    txt = (raw or "").strip()
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            sev = str(obj.get("severity", "ok")).lower()
            if sev not in _SEV_TO_RC:
                sev = "warning" if sev not in ("ok", "info") else "ok"
            return sev, str(obj.get("reason", ""))[:300]
        except Exception:
            pass
    low = txt.lower()
    if "critical" in low:
        return "critical", txt[:200]
    if "warning" in low:
        return "warning", txt[:200]
    return "ok", txt[:200]


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
async def run_monitor(mon: "LogAnomalyMonitor", db) -> Detection:
    kind = (mon.kind or "baseline").lower()
    if kind == "novelty":
        return await run_novelty(mon, db)
    if kind == "watch":
        return await run_watch(mon, db)
    return await asyncio.to_thread(run_baseline, mon)


def _should_run(mon: "LogAnomalyMonitor", now: datetime) -> bool:
    interval = int(getattr(mon, "min_interval_sec", 0) or 0)
    if interval <= 0:
        return True
    lr = mon.last_run
    if lr is None:
        return True
    if lr.tzinfo is None:
        lr = lr.replace(tzinfo=timezone.utc)
    return (now - lr).total_seconds() >= interval


async def _record_and_notify(mon: "LogAnomalyMonitor", det: Detection, db) -> None:
    now = datetime.now(timezone.utc)
    prev = mon.last_state
    if prev != det.rc:
        db.add(LogAnomalyEvent(
            monitor_id=mon.id, monitor_name=mon.name, kind=(mon.kind or "baseline"),
            host=getattr(mon, "host_binding", None), severity=mon.severity,
            state=det.rc, prev_state=prev, score=det.score, observed=det.observed,
            baseline_mean=det.baseline_mean, baseline_std=det.baseline_std,
            template=det.template, sample=det.sample, output=det.output,
        ))
        mon.last_state = det.rc
    mon.last_score = det.score
    mon.last_run = now

    host = getattr(mon, "host_binding", None)
    if host:
        try:
            valid = _validate_host_name(host)
            known = host_exists(valid)
        except InvalidHostName:
            valid, known = None, False
        if known:
            svc = service_name(slugify_rule_name(mon.name))
            await asyncio.to_thread(submit_passive_result, valid, svc, det.rc, det.output)
        else:
            log.debug("anomaly monitor %r: host_binding %r unknown; skip passive",
                      mon.name, host)


async def evaluate_all(session_factory) -> None:
    """Evaluate every enabled anomaly monitor once (called from the evaluator)."""
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        mons = (await db.execute(
            select(LogAnomalyMonitor).where(LogAnomalyMonitor.enabled.is_(True))
        )).scalars().all()
        for mon in mons:
            if not _should_run(mon, now):
                continue
            try:
                det = await run_monitor(mon, db)
                await _record_and_notify(mon, det, db)
                await db.commit()
            except Exception as e:
                log.exception("anomaly monitor %s failed: %s", mon.name, e)
                await db.rollback()

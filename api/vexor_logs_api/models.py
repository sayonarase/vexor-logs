"""SQLAlchemy models for log alerts + saved searches."""
from __future__ import annotations
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, func
try:
    from app.database import Base  # type: ignore
except Exception:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()


class LogAlertRule(Base):
    __tablename__ = 'log_alert_rules'
    id           = Column(Integer, primary_key=True)
    name         = Column(String(255), nullable=False, unique=True)
    query        = Column(Text, nullable=False)
    window_sec   = Column(Integer, nullable=False, default=300)
    threshold    = Column(Integer, nullable=False, default=1)
    severity     = Column(String(32), nullable=False, default='warning')
    notify_to    = Column(String(255), nullable=False, default='')
    host_binding = Column(String(255), nullable=True)
    enabled      = Column(Boolean, nullable=False, default=True)
    # --- log-check enhancements (migration 003) ---
    # mode: 'match'   -> fire when match count crosses thresholds
    #       'absence' -> fire when too FEW logs arrive (dead-man / "logs stopped")
    mode           = Column(String(16), nullable=False, default='match')
    warn_threshold = Column(Integer, nullable=True)
    crit_threshold = Column(Integer, nullable=True)
    level_filter   = Column(String(32), nullable=True)
    group_by_host  = Column(Boolean, nullable=False, default=False)
    preset_id      = Column(String(64), nullable=True)
    last_fired   = Column(DateTime(timezone=True), nullable=True)
    last_count   = Column(Integer, nullable=False, default=0)
    # Last Naemon return code submitted (migration 005), used to detect
    # OK<->WARN<->CRIT transitions for the alert-history log.
    last_state   = Column(Integer, nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LogAlertEvent(Base):
    """Append-only history of log-alert state changes (migration 005).

    One row is written each time a rule's evaluated state changes (e.g. OK->
    CRITICAL when a match threshold is crossed or a host stops shipping logs,
    and CRITICAL->OK on recovery). Powers the per-host / per-rule history view.
    """
    __tablename__ = 'log_alert_events'
    id         = Column(Integer, primary_key=True)
    rule_id    = Column(Integer, nullable=True, index=True)
    rule_name  = Column(String(255), nullable=False)
    host       = Column(String(255), nullable=True, index=True)
    mode       = Column(String(16), nullable=False, default='match')
    severity   = Column(String(32), nullable=True)
    state      = Column(Integer, nullable=False)          # naemon rc 0/1/2
    prev_state = Column(Integer, nullable=True)
    count      = Column(Integer, nullable=False, default=0)
    output     = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class LogSavedSearch(Base):
    __tablename__ = 'log_saved_searches'
    id          = Column(Integer, primary_key=True)
    name        = Column(String(255), nullable=False, unique=True)
    query       = Column(Text, nullable=False)
    time_range  = Column(String(64), nullable=False, default='1h')
    created_by  = Column(String(128), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LogRetentionOverride(Base):
    """Per-host retention exception (migration 004).

    Global retention (-retentionPeriod) is the ceiling that applies to every
    host. An override lets an operator keep a single host's logs for a SHORTER
    time than the global setting; the daily retention enforcer deletes that
    host's logs older than ``retention_days``. To keep a host LONGER than the
    rest, raise the global retention (the global is always the maximum).
    """
    __tablename__ = 'log_retention_overrides'
    id             = Column(Integer, primary_key=True)
    host           = Column(String(255), nullable=False, unique=True)
    retention_days = Column(Integer, nullable=False)
    note           = Column(String(255), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class LogAnomalyMonitor(Base):
    """Configurable anomaly detector (migration 006).

    kind:
      * 'baseline' - statistical volume/rate anomaly (robust z-score)
      * 'novelty'  - new log-template detection
      * 'watch'    - natural-language concern evaluated by the LLM
    """
    __tablename__ = 'log_anomaly_monitors'
    id            = Column(Integer, primary_key=True)
    name          = Column(String(255), nullable=False, unique=True)
    kind          = Column(String(16), nullable=False, default='baseline')
    query         = Column(Text, nullable=False, default='*')
    enabled       = Column(Boolean, nullable=False, default=True)
    window_sec    = Column(Integer, nullable=False, default=300)
    baseline_sec  = Column(Integer, nullable=False, default=86400)
    sensitivity   = Column(Float, nullable=False, default=3.0)
    direction     = Column(String(8), nullable=False, default='both')  # spike|drop|both
    min_baseline  = Column(Float, nullable=False, default=0)
    min_interval_sec = Column(Integer, nullable=False, default=0)
    severity      = Column(String(32), nullable=False, default='warning')
    host_binding  = Column(String(255), nullable=True)
    nl_question   = Column(Text, nullable=True)
    preset_id     = Column(String(64), nullable=True)
    last_state    = Column(Integer, nullable=True)
    last_score    = Column(Float, nullable=True)
    last_run      = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LogAnomalyEvent(Base):
    """Append-only history of anomaly detections (migration 006)."""
    __tablename__ = 'log_anomaly_events'
    id            = Column(Integer, primary_key=True)
    monitor_id    = Column(Integer, nullable=True, index=True)
    monitor_name  = Column(String(255), nullable=False)
    kind          = Column(String(16), nullable=False, default='baseline')
    host          = Column(String(255), nullable=True, index=True)
    severity      = Column(String(32), nullable=True)
    state         = Column(Integer, nullable=False)          # naemon rc 0/1/2/3
    prev_state    = Column(Integer, nullable=True)
    score         = Column(Float, nullable=True)
    observed      = Column(Float, nullable=True)
    baseline_mean = Column(Float, nullable=True)
    baseline_std  = Column(Float, nullable=True)
    template      = Column(Text, nullable=True)
    sample        = Column(Text, nullable=True)
    output        = Column(Text, nullable=True)
    llm_note      = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class LogAnomalyTemplate(Base):
    """Known log templates per novelty monitor (migration 006)."""
    __tablename__ = 'log_anomaly_templates'
    id         = Column(Integer, primary_key=True)
    monitor_id = Column(Integer, nullable=False, index=True)
    signature  = Column(String(40), nullable=False)
    template   = Column(Text, nullable=True)
    hits       = Column(Integer, nullable=False, default=1)
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Log field-extraction / parse rules (migration 007, feature F2)
# ---------------------------------------------------------------------------
class LogParseRule(Base):
    """User-defined field extraction applied to search results on demand.

    A parse rule turns unstructured ``_msg`` text into named fields using a
    VictoriaLogs ``extract`` pattern (``pattern_type='pattern'``, e.g.
    ``ip=<ip> status=<status>``) or an ``extract_regexp`` regular expression
    (``pattern_type='regexp'`` with named groups). Rules are expanded into
    LogsQL pipes by the ``/logs/parse-rules/expand`` endpoint and appended to
    the base query in the search view.
    """
    __tablename__ = 'log_parse_rules'
    id           = Column(Integer, primary_key=True)
    name         = Column(String(255), nullable=False, unique=True)
    source_field = Column(String(64), nullable=False, default='_msg')
    pattern      = Column(Text, nullable=False)
    pattern_type = Column(String(16), nullable=False, default='pattern')  # pattern|regexp
    enabled      = Column(Boolean, nullable=False, default=True)
    sort_order   = Column(Integer, nullable=False, default=100)
    note         = Column(String(255), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Log-derived metrics (migration 008, feature F3)
# ---------------------------------------------------------------------------
class LogMetric(Base):
    """A graphable metric derived from a periodically-evaluated log query.

    Every ``window_sec`` the evaluator counts matches of ``query`` in the
    trailing window (optionally grouped by ``group_by``) and stores a sample.
    ``agg='rate'`` divides the count by the window length (per-second). When
    ``host_binding`` is set the latest value is also submitted to Naemon as a
    passive service check with perfdata, so it graphs and can alert via
    ``warn_threshold`` / ``crit_threshold``.
    """
    __tablename__ = 'log_metrics'
    id             = Column(Integer, primary_key=True)
    name           = Column(String(255), nullable=False, unique=True)
    query          = Column(Text, nullable=False, default='*')
    agg            = Column(String(16), nullable=False, default='count')   # count|rate
    window_sec     = Column(Integer, nullable=False, default=300)
    group_by       = Column(String(64), nullable=True)
    unit           = Column(String(32), nullable=True)
    enabled        = Column(Boolean, nullable=False, default=True)
    warn_threshold = Column(Float, nullable=True)
    crit_threshold = Column(Float, nullable=True)
    severity       = Column(String(32), nullable=False, default='warning')
    host_binding   = Column(String(255), nullable=True)
    last_value     = Column(Float, nullable=True)
    last_state     = Column(Integer, nullable=True)
    last_run       = Column(DateTime(timezone=True), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LogMetricSample(Base):
    """Time-series samples produced by a LogMetric (migration 008)."""
    __tablename__ = 'log_metric_samples'
    id         = Column(Integer, primary_key=True)
    metric_id  = Column(Integer, nullable=False, index=True)
    ts         = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    host       = Column(String(255), nullable=True, index=True)
    value      = Column(Float, nullable=False, default=0)


# ---------------------------------------------------------------------------
# Scheduled log digests / reports (migration 009, feature F8)
# ---------------------------------------------------------------------------
class LogReport(Base):
    """A scheduled digest summarising a log query over a window.

    On its schedule the evaluator composes a summary (total volume, top values
    of ``top_field``, error count) and delivers it as a notification through
    vexor-api's internal dispatch endpoint (routed by the operator's
    notification policies). ``schedule_kind`` is ``daily`` / ``weekly`` /
    ``interval``.
    """
    __tablename__ = 'log_reports'
    id             = Column(Integer, primary_key=True)
    name           = Column(String(255), nullable=False, unique=True)
    enabled        = Column(Boolean, nullable=False, default=True)
    query          = Column(Text, nullable=False, default='*')
    window_sec     = Column(Integer, nullable=False, default=86400)
    schedule_kind  = Column(String(16), nullable=False, default='daily')  # daily|weekly|interval
    at_hour        = Column(Integer, nullable=False, default=7)
    at_minute      = Column(Integer, nullable=False, default=0)
    dow            = Column(Integer, nullable=True)                       # 0=Mon..6=Sun (weekly)
    interval_hours = Column(Integer, nullable=True)
    top_field      = Column(String(64), nullable=False, default='host')
    error_query    = Column(Text, nullable=True)
    severity       = Column(String(32), nullable=False, default='info')
    recipients     = Column(String(255), nullable=True)
    last_run       = Column(DateTime(timezone=True), nullable=True)
    next_run       = Column(DateTime(timezone=True), nullable=True)
    last_output    = Column(Text, nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

"""Vexor Logs API plugin.

Mounted at startup by vexor-api's plugin loader. Exposes:
  * GET  /api/v1/logs/query           — proxy to VictoriaLogs LogsQL
  * GET  /api/v1/logs/tail            — SSE live-tail
  * GET  /api/v1/logs/streams         — label/stream discovery
  * GET  /api/v1/logs/histogram       — bucketed match counts
  * GET  /api/v1/logs/export          — CSV / NDJSON download
  * POST /api/v1/logs/test-query      — preview a query before saving
  * GET/PUT /api/v1/logs/settings     — retention + env config
  * GET  /api/v1/logs/storage         — disk usage + oldest log
  * CRUD /api/v1/logs/saved-searches  — saved searches
  * GET  /api/v1/logs/filter-library  — curated starter filters
  * POST /api/v1/logs/deploy-shipper  — remote install of vector / fluent-bit
  * CRUD /api/v1/log-alerts           — alert rule management
  * GET  /api/v1/log-checks/catalog   — log-check presets (filters + dead-man)
  * POST /api/v1/log-checks/for-host  — apply log checks to a host
  * GET  /api/v1/modules              — module discovery (advertises "logs")
  * POST /api/v1/logs/ai-analyze      — LLM-assisted triage of a log query
"""
from .logs_router import router as logs_router                       # noqa: F401
from .log_alerts_router import router as log_alerts_router           # noqa: F401
from .log_checks_router import router as log_checks_router           # noqa: F401
from .retention_router import router as retention_router             # noqa: F401
from .modules_router import router as modules_router                 # noqa: F401
from .settings_router import router as settings_router               # noqa: F401
from .saved_searches_router import router as saved_searches_router   # noqa: F401
from .filter_library_router import router as filter_library_router   # noqa: F401
from .shipper_router import router as shipper_router                 # noqa: F401
from .log_ai_router import router as log_ai_router                   # noqa: F401

# vexor-api's plugin loader looks for a module-level `routers` list.
routers = [
    logs_router,
    log_alerts_router,
    log_checks_router,
    retention_router,
    modules_router,
    settings_router,
    saved_searches_router,
    filter_library_router,
    shipper_router,
    log_ai_router,
]


def start_background_tasks(app):
    # The alert evaluator runs as its own systemd unit (vexor-log-alerts-evaluator);
    # nothing to start in-process. Hook left for symmetry with other modules.
    return None

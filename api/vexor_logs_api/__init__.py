"""Vexor Logs API plugin.

Mounted at startup by vexor-api's plugin loader. Exposes:
  * GET  /v1/logs/query    — proxy to VictoriaLogs LogsQL
  * GET  /v1/logs/tail     — SSE live-tail
  * GET  /v1/logs/streams  — label/stream discovery
  * CRUD /v1/log-alerts    — alert rule management
  * GET  /v1/modules       — module discovery (advertises "logs")
"""
from .logs_router import router as logs_router          # noqa: F401
from .log_alerts_router import router as log_alerts_router  # noqa: F401
from .modules_router import router as modules_router    # noqa: F401

# vexor-api's plugin loader looks for a module-level `routers` list.
routers = [logs_router, log_alerts_router, modules_router]

# Optional symbol the loader checks: list of background tasks to start.
def start_background_tasks(app):
    # Alert evaluation runs as its own systemd unit (vexor-log-alerts-evaluator);
    # nothing to start in-process. Hook left for symmetry with other modules.
    return None

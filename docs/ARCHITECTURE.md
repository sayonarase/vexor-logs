# Architecture

## Why VictoriaLogs?

* Single Go binary, ~30 MiB, no clustering required for MVP.
* Apache 2.0 (matches the licence we ship our own glue under).
* LogsQL is concise and well documented; ingest is compatible with the
  Loki HTTP push API, so we can reuse Vector and Fluent Bit sinks
  unchanged.
* Disk-friendly: typical compression of 10–20x for syslog-style data.

## Modes

* **Integrated (Fas 1, this release).** Runs inside an existing Vexor
  server. The vexor-logs RPM drops a FastAPI plugin into
  `/opt/vexor/api/plugins/logs/` and the vexor-api service mounts it on
  startup. UI pages are part of vexor-ui and gated by `/v1/modules`.
* **Standalone (Fas 2, future).** Same RPMs but installable without
  vexor-api/vexor-ui present; the plugin gains a tiny FastAPI app of its
  own.

## Plugin discovery

`vexor-api`'s `app/main.py` runs the small loader added in 0.1.0-6:

```python
for path in glob('/opt/vexor/api/plugins/*/router.py'):
    mod = importlib.import_module(...); 
    for r in getattr(mod, 'routers', [getattr(mod, 'router', None)]):
        if r is not None:
            app.include_router(r)
```

The plugin exports a `routers` list (`logs`, `log-alerts`, `modules`).

## Data flow

```
agents  --(Loki push, JSONL)--> VictoriaLogs :9428
                                      ^
                                      |  HTTP
                            vexor-api /v1/logs/* (proxy)
                                      ^
                                      |  axios
                              vexor-ui /logs page
```

## Alert evaluation

The `vexor-log-alerts-evaluator` systemd unit imports the same SQLAlchemy
models the API uses, walks every enabled `log_alert_rules` row every 30 s,
runs the LogsQL query for the configured window, and POSTs to vexor-api's
existing `/v1/notifications/dispatch` if the count exceeds the threshold.

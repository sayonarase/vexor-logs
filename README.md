# Vexor Logs

Integrated + standalone log-management addon for the
[Vexor monitoring platform](https://github.com/sayonarase), backed by
[VictoriaLogs](https://github.com/VictoriaMetrics/VictoriaLogs).

This is **Fas 1 (integrated mode)** — the components below install as RPM
add-ons to an existing Vexor server and plug into the existing API and UI
via a plugin mechanism.

## Components

| RPM                  | What it provides                                              |
|----------------------|---------------------------------------------------------------|
| `vexor-victorialogs` | VictoriaLogs daemon, default config, systemd unit, loopback   |
| `vexor-vector`       | Wrapper RPM around upstream Vector with Vexor default config  |
| `vexor-fluentbit`    | Wrapper RPM around upstream Fluent Bit with default config    |
| `vexor-logs`         | Meta-package + API plugin + alert evaluator (server-side)     |

## How it integrates

```
            ┌──────────────────┐
hosts ───▶  │ vector / fluentbit│ ──HTTP(Loki)──▶ VictoriaLogs ◀──┐
            └──────────────────┘                     ▲             │
                                                     │             │
                                                vexor-api          │
                                              (plugin loader)──────┘
                                                     ▲
                                                     │
                                                  vexor-ui
                                            (Logs / LogAlerts pages)
```

* `vexor-api` scans `/opt/vexor/api/plugins/*/router.py` on startup and mounts
  any FastAPI `router` it finds. `vexor-logs` drops its router there.
* The UI gates new sidebar entries on `/v1/modules` returning `"logs"`.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/INSTALL.md`](docs/INSTALL.md) for details. Example alert rules live in
[`docs/ALERT-EXAMPLES.md`](docs/ALERT-EXAMPLES.md).

## License

Apache 2.0 — matches the VictoriaLogs backend license.

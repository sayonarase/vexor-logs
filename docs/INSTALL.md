# Installing Vexor Logs

## On the Vexor server

```bash
dnf install vexor-logs    # pulls vexor-victorialogs as a dependency
systemctl enable --now vexor-victorialogs
systemctl enable --now vexor-log-alerts-evaluator
systemctl restart vexor-api
```

That is it. The vexor-api plugin loader will pick up
`/opt/vexor/api/plugins/logs/` on the next restart, the UI's sidebar will
gain a *Logs* section once `/v1/modules` returns `"logs"`, and the alert
evaluator runs as its own systemd unit.

## On each host you want to ship logs from

Pick **one** agent:

```bash
dnf install vexor-vector        # Vector (MPL-2.0) — recommended
# – or –
dnf install vexor-fluentbit     # Fluent Bit (Apache 2.0)
```

Then edit `/etc/vexor/logs.env`:

```
VEXOR_LOGS_URL=http://vexor-server.example.com:9428
VEXOR_LOGS_HOST=vexor-server.example.com
VEXOR_LOGS_PORT=9428
```

…and start the unit:

```bash
systemctl enable --now vexor-vector      # or vexor-fluentbit
```

Both default configs read `/var/log/messages`, `/var/log/secure`, the
audit log and the journald journal, and ship them to VictoriaLogs over
the Loki-compatible push API.

## Opening the firewall

On the Vexor server, expose VictoriaLogs (TCP 9428) only to your hosts.
The default config binds to 127.0.0.1; either change `-httpListenAddr` in
`/usr/lib/systemd/system/vexor-victorialogs.service` or front it with the
existing Vexor reverse-proxy.

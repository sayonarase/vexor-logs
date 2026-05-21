# Vexor Logs Agents

Vexor Logs ships logs from your hosts into the central VictoriaLogs store
through a thin agent. Two are supported:

| Agent       | Linux | Windows | Recommended for                |
| ----------- | :---: | :-----: | ------------------------------ |
| **vector**  |  ✅   |   ✅    | Default / everything           |
| fluent-bit  |  ✅   |   —     | Minimal-footprint edge devices |

The Vexor server hosts a reverse-proxied ingest endpoint at
`POST /api/v1/logs/push` which translates to the VictoriaLogs JSON push API
on port `9428`.

## Quick install — Linux

The recommended path is the bundled installer that auto-detects the
package manager (dnf / yum / apt) and writes a ready-to-go config:

```bash
curl -fsSL https://<vexor>/api/v1/logs/install-scripts/install-linux-agent.sh \
  | sudo bash -s -- \
      --vexor-url https://<vexor> \
      --token <bootstrap-token> \
      --agent vector \
      --log /var/log
```

If your Vexor server publishes its own RPM mirror you can also install
the upstream packages directly:

```bash
sudo dnf install vexor-vector       # ships /etc/vexor/logs/vector.toml
sudo systemctl enable --now vexor-vector
sudo journalctl -u vexor-vector -n50
```

The standalone `vector` (or `fluent-bit`) systemd unit reads the
`vector.toml` written by the installer. Edit
`/etc/vexor/logs.env` to change the upstream URL or token, then restart:

```bash
sudo systemctl restart vector
```

### Manual install (Linux)

Download the matching `vector` binary from
<https://vector.dev/releases>, drop it in `/usr/local/bin`, and place
`/etc/vector/vector.toml` (template below). A minimal systemd unit:

```ini
[Unit]
Description=Vector log shipper
After=network-online.target
[Service]
EnvironmentFile=-/etc/vexor/logs.env
ExecStart=/usr/local/bin/vector --config /etc/vector/vector.toml
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

Minimal vector.toml:

```toml
data_dir = "/var/lib/vector"

[sources.files]
type    = "file"
include = ["/var/log/**/*.log"]

[sources.journald]
type = "journald"

[transforms.add_host]
type    = "remap"
inputs  = ["files", "journald"]
source  = '.host = get_hostname!()'

[sinks.vexor]
type   = "http"
inputs = ["add_host"]
uri    = "https://<vexor>/api/v1/logs/push?_stream_fields=host,file"
encoding.codec = "json"
framing.method = "newline_delimited"
compression = "gzip"
```

## Quick install — Windows

Open an **elevated PowerShell** on the target and run:

```powershell
iwr https://<vexor>/api/v1/logs/install-scripts/install-windows-agent.ps1 `
    -OutFile $env:TEMP\vexor-vector.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\vexor-vector.ps1 `
    -VexorUrl https://<vexor> -Token <bootstrap-token> -Logs Application,System,Security
```

The installer:

1. Downloads the official `vector.exe` (default version pinned in the
   script).
2. Writes `C:\ProgramData\Vexor\vector\conf\vector.toml` configured to
   read the requested Windows Event Log channels.
3. Installs **NSSM** as `C:\Program Files\Vexor\vector\nssm.exe`.
4. Registers `vexor-vector` as an auto-start Windows service.

Inspect the service with:

```powershell
Get-Service vexor-vector
Get-Content "C:\ProgramData\Vexor\vector\vector.log" -Tail 50
```

### Alternative: fluent-bit on Windows

Only available via the manual download from
<https://docs.fluentbit.io/manual/installation/windows>. Re-use the
`[OUTPUT]` block emitted by the Linux installer.

## GUI deploy (recommended for many hosts)

For administrators with SSH or WinRM access already configured in
**Settings → Credentials**, the Vexor UI has a **Logs › Shippers** page
that:

* Lists every host known to Vexor with their last-seen state.
* Lets you pick `vector` or `fluentbit`, the credential to use, and the
  set of paths/channels to ship.
* Calls `POST /api/v1/logs/deploy-shipper` which executes the same
  install script on the target and returns stdout/stderr to the UI.

WinRM remote execution is not yet wired server-side; the page will
return the exact PowerShell one-liner you should paste on the target.

## Verifying ingest

From the Vexor server:

```bash
curl -sk https://localhost/api/v1/logs/storage | jq
curl -sk "https://localhost/api/v1/logs/query?query=host:<your-host>&limit=10" | jq
```

You should see the host's log lines arrive within a few seconds of the
agent starting.

#!/usr/bin/env bash
# install-linux-agent.sh — Vexor Logs shipper installer.
#
# Usage:
#   install-linux-agent.sh --vexor-url URL [--token TOKEN] [--agent vector|fluentbit]
#                          [--log /path]...
#
# Verbose tracing: VERBOSE=1 install-linux-agent.sh ...

set -euo pipefail

VECTOR_VERSION="${VECTOR_VERSION:-0.44.0}"
AGENT="vector"
TOKEN=""
URL=""
LOGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vexor-url) URL="$2"; shift 2 ;;
    --token)     TOKEN="$2"; shift 2 ;;
    --agent)     AGENT="$2"; shift 2 ;;
    --log|--logs) LOGS+=("$2"); shift 2 ;;
    --help|-h)   sed -n "2,8p" "$0"; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$URL" ]] && { echo "ERROR: --vexor-url is required" >&2; exit 2; }
[[ ${#LOGS[@]} -eq 0 ]] && LOGS=("/var/log")

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi
[[ "${VERBOSE:-0}" == "1" ]] && set -x

log()   { printf '\e[36m>>> %s\e[0m\n' "$*"; }
ok()    { printf '\e[32m[OK]\e[0m %s\n' "$*"; }
warn()  { printf '\e[33m[WARN]\e[0m %s\n' "$*" >&2; }
fail()  { printf '\e[31m[FAIL]\e[0m %s\n' "$*" >&2; }

on_err() {
  local rc=$?
  local line=$1
  fail "step failed (rc=$rc) at line $line: ${BASH_COMMAND:-?}"
  exit "$rc"
}
trap 'on_err $LINENO' ERR

VEXOR_HOST="$(echo "$URL" | sed -E 's#^https?://##; s#/.*##')"
HOST_NAME="$(hostname)"
HOST_ADDR="$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}')"
[[ -z "$HOST_ADDR" ]] && HOST_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$HOST_ADDR" ]] && HOST_ADDR="unknown"

log "Installing $AGENT on $HOST_NAME -> $URL"
log "  log paths: ${LOGS[*]}"
log "  agent ver: $VECTOR_VERSION"

# ---- Detect package manager ------------------------------------------------
PKG=""
if   command -v dnf >/dev/null 2>&1; then PKG=dnf
elif command -v yum >/dev/null 2>&1; then PKG=yum
elif command -v apt-get >/dev/null 2>&1; then PKG=apt-get
fi
log "Package manager: ${PKG:-<none detected>}"

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) PKG_ARCH=x86_64 ;;
  aarch64|arm64) PKG_ARCH=aarch64 ;;
  *) fail "unsupported arch: $ARCH"; exit 3 ;;
esac

install_vector_direct_rpm() {
  local url="https://packages.timber.io/vector/${VECTOR_VERSION}/vector-${VECTOR_VERSION}-1.${PKG_ARCH}.rpm"
  log "Installing vector $VECTOR_VERSION from direct RPM:"
  log "  $url"
  if ! $SUDO ${PKG} install -y "$url" 2>&1 | sed 's/^/    /'; then
    warn "package manager install failed, falling back to rpm -Uvh"
    local tmp; tmp="$(mktemp --suffix=.rpm)"
    curl -fSL --progress-bar -o "$tmp" "$url"
    $SUDO rpm -Uvh --force "$tmp" | sed 's/^/    /'
    rm -f "$tmp"
  fi
}

install_vector_direct_deb() {
  local arch=amd64; [[ "$PKG_ARCH" == "aarch64" ]] && arch=arm64
  local url="https://packages.timber.io/vector/${VECTOR_VERSION}/vector_${VECTOR_VERSION}-1_${arch}.deb"
  local tmp; tmp="$(mktemp --suffix=.deb)"
  log "Installing vector $VECTOR_VERSION from direct .deb"
  log "  $url"
  curl -fSL --progress-bar -o "$tmp" "$url"
  $SUDO apt-get install -y "$tmp" | sed 's/^/    /'
  rm -f "$tmp"
}

install_vector() {
  if command -v vector >/dev/null 2>&1; then
    ok "vector already installed: $(vector --version | head -1)"; return
  fi
  case "$PKG" in
    dnf|yum) install_vector_direct_rpm ;;
    apt-get) install_vector_direct_deb ;;
    *) fail "no supported package manager"; exit 3 ;;
  esac
  if command -v vector >/dev/null 2>&1; then
    ok "vector installed: $(vector --version | head -1)"
  else
    fail "vector binary missing after install"
    exit 4
  fi
}

install_fluentbit() {
  if command -v fluent-bit >/dev/null 2>&1; then
    ok "fluent-bit already installed"; return
  fi
  case "$PKG" in
    dnf|yum)
      $SUDO tee /etc/yum.repos.d/fluent-bit.repo >/dev/null <<EOF
[fluent-bit]
name=Fluent Bit
baseurl=https://packages.fluentbit.io/centos/\$releasever/\$basearch
gpgcheck=0
enabled=1
EOF
      $SUDO $PKG install -y fluent-bit | sed 's/^/    /'
      ;;
    apt-get)
      curl -fsSL https://packages.fluentbit.io/fluentbit.key | $SUDO gpg --dearmor -o /usr/share/keyrings/fluentbit.gpg
      echo "deb [signed-by=/usr/share/keyrings/fluentbit.gpg] https://packages.fluentbit.io/ubuntu/$(. /etc/os-release && echo $UBUNTU_CODENAME) main" | $SUDO tee /etc/apt/sources.list.d/fluent-bit.list
      $SUDO apt-get update && $SUDO apt-get install -y fluent-bit | sed 's/^/    /'
      ;;
    *) fail "no supported package manager"; exit 3 ;;
  esac
}

write_vector_config() {
  log "Writing /etc/vector/vector.toml"
  $SUDO install -d -m 0755 /etc/vexor /etc/vector /var/lib/vector
  # Disable the default demo config so vector loads our vector.toml
  if [[ -f /etc/vector/vector.yaml ]]; then
    $SUDO mv /etc/vector/vector.yaml /etc/vector/vector.yaml.disabled
    log "  (renamed default vector.yaml -> vector.yaml.disabled)"
  fi
  local include_lines=""
  for l in "${LOGS[@]}"; do
    include_lines+="\"$l/**/*.log\","
    [[ -f "$l" ]] && include_lines+="\"$l\","
  done
  include_lines="${include_lines%,}"
  $SUDO tee /etc/vexor/logs.env >/dev/null <<EOF
VEXOR_LOGS_URL=${URL}
VEXOR_LOGS_TOKEN=${TOKEN}
EOF
  $SUDO chmod 0600 /etc/vexor/logs.env
  $SUDO chown root:root /etc/vexor/logs.env
  $SUDO tee /etc/vector/vector.toml >/dev/null <<EOF
# Generated by vexor install-linux-agent.sh on $(date -Is)
data_dir = "/var/lib/vector"

[sources.files]
type    = "file"
include = [${include_lines}]
ignore_older_secs = 86400

[sources.journald]
type = "journald"
current_boot_only = true

[transforms.add_host]
type    = "remap"
inputs  = ["files", "journald"]
source  = '''
.host = "${HOST_NAME}"
.address = "${HOST_ADDR}"
'''

[sinks.vexor]
type    = "http"
inputs  = ["add_host"]
uri     = "${URL}/api/v1/logs/push?_stream_fields=host,address,file&_msg_field=message&_time_field=timestamp"
encoding.codec = "json"
framing.method = "newline_delimited"
compression = "gzip"
healthcheck.enabled = false
$( [[ -n "$TOKEN" ]] && printf 'request.headers.Authorization = "Bearer %s"\n' "$TOKEN" )

[sinks.vexor.tls]
verify_certificate = false
EOF
  $SUDO chmod 0600 /etc/vector/vector.toml
  $SUDO chown root:root /etc/vector/vector.toml
  ok "config written"
}

write_fluentbit_config() {
  log "Writing /etc/fluent-bit/fluent-bit.conf"
  $SUDO install -d -m 0755 /etc/vexor /etc/fluent-bit
  local inputs=""
  for l in "${LOGS[@]}"; do
    inputs+="
[INPUT]
    Name        tail
    Path        ${l}/*.log
    Tag         vexor.${l##*/}
    Refresh_Interval 5
"
  done
  $SUDO tee /etc/fluent-bit/fluent-bit.conf >/dev/null <<EOF
[SERVICE]
    Flush       5
    Log_Level   info
${inputs}
[OUTPUT]
    Name        http
    Match       *
    Host        ${VEXOR_HOST%:*}
    Port        $(echo "${VEXOR_HOST##*:}" | grep -Eo '^[0-9]+$' || echo 443)
    URI         /api/v1/logs/push
    Format      json_lines
    tls         on
    tls.verify  off
$( [[ -n "$TOKEN" ]] && echo "    Header      Authorization Bearer ${TOKEN}" )
EOF
  $SUDO chmod 0600 /etc/fluent-bit/fluent-bit.conf
  $SUDO chown root:root /etc/fluent-bit/fluent-bit.conf
  ok "fluent-bit config written"
}

log "Step 1/4 - install agent"
case "$AGENT" in
  vector)              install_vector ;;
  fluentbit|fluent-bit) install_fluentbit; AGENT=fluent-bit ;;
  *) fail "unknown agent: $AGENT"; exit 2 ;;
esac

log "Step 2/4 - write configuration"
case "$AGENT" in
  vector)     write_vector_config ;;
  fluent-bit) write_fluentbit_config ;;
esac

log "Step 3/4 - enable + start service"
# Run vector as root so it can read /var/log/* and journald
$SUDO install -d -m 0755 /etc/systemd/system/vector.service.d
$SUDO tee /etc/systemd/system/vector.service.d/vexor.conf >/dev/null <<EOF
[Service]
User=root
Group=root
ExecStartPre=
ExecStartPre=/usr/bin/vector validate --config-toml /etc/vector/vector.toml
ExecStart=
ExecStart=/usr/bin/vector --config-toml /etc/vector/vector.toml
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$AGENT" 2>&1 | sed 's/^/    /'
$SUDO systemctl restart "$AGENT"
sleep 2

log "Step 4/4 - verify"
if $SUDO systemctl is-active --quiet "$AGENT"; then
  ok "$AGENT is active"
  $SUDO systemctl status "$AGENT" --no-pager -n 5 | sed 's/^/    /'
else
  fail "$AGENT failed to start"
  $SUDO journalctl -u "$AGENT" -n 30 --no-pager | sed 's/^/    /'
  exit 5
fi

ok "Done. Logs from $HOST_NAME are now being shipped to $URL"
echo "    Inspect locally: journalctl -u $AGENT -f"
echo "    Inspect in GUI:  $URL/logs?host=$HOST_NAME"

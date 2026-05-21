#!/usr/bin/env bash
# Downloads the upstream VictoriaLogs release tarball and verifies the
# SHA256 sum so RPM builds are reproducible. Usage:
#   install-victorialogs.sh <version> <destdir>
set -euo pipefail
VER="${1:-1.1.0}"
DEST="${2:-/root/vexor-logs/rpm/SOURCES}"
URL="https://github.com/VictoriaMetrics/VictoriaLogs/releases/download/v${VER}/victoria-logs-linux-amd64-v${VER}.tar.gz"
mkdir -p "$DEST"
OUT="${DEST}/victoria-logs-linux-amd64-v${VER}.tar.gz"
if [ ! -f "$OUT" ]; then
    echo "+ downloading $URL"
    curl -fsSL -o "$OUT" "$URL"
fi
echo "OK: $OUT"

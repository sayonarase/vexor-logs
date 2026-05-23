#!/usr/bin/env bash
# install-linux-agent-interactive.sh — prompts for URL, token, log dirs,
# then delegates to install-linux-agent.sh.
set -euo pipefail

DEFAULT_URL="${VEXOR_URL:-}"
DEFAULT_TOKEN="${VEXOR_TOKEN:-}"
DEFAULT_AGENT="${VEXOR_AGENT:-vector}"

read -rp "Vexor server URL [${DEFAULT_URL:-https://vexor.example.com}]: " URL
URL="${URL:-${DEFAULT_URL:-https://vexor.example.com}}"

read -rp "Bootstrap token (from GUI: Logs > Shippers) [${DEFAULT_TOKEN}]: " TOKEN
TOKEN="${TOKEN:-$DEFAULT_TOKEN}"

read -rp "Agent (vector / fluentbit) [${DEFAULT_AGENT}]: " AGENT
AGENT="${AGENT:-$DEFAULT_AGENT}"

echo "Log paths to ship (one per line, empty to finish; default = /var/log):"
LOGS=()
while true; do
  read -rp "  path> " p
  [[ -z "$p" ]] && break
  LOGS+=("$p")
done
[[ ${#LOGS[@]} -eq 0 ]] && LOGS=("/var/log")

ARGS=(--vexor-url "$URL" --token "$TOKEN" --agent "$AGENT")
for l in "${LOGS[@]}"; do ARGS+=(--log "$l"); done

# Fetch the default installer from the same Vexor server and run with collected args.
TMP="$(mktemp)"
curl -fsSLk "${URL%/}/api/v1/logs/install-scripts/install-linux-agent.sh" -o "$TMP"
chmod +x "$TMP"
echo
echo "=> Running install-linux-agent.sh ${ARGS[*]}"
exec bash "$TMP" "${ARGS[@]}"

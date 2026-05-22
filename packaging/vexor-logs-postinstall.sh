#!/bin/bash
# vexor-logs RPM postinstall: install polkit rule, apply migrations, reload polkit.
set -euo pipefail

# Reload polkit so the new rule takes effect immediately (best effort).
systemctl reload polkit 2>/dev/null || systemctl restart polkit 2>/dev/null || true

# Apply migrations from /usr/share/vexor-logs/migrations/. Idempotent — safe to
# rerun on every upgrade. We pull DB creds from /etc/vexor/db.env which is
# laid down by vexor-api on first install.
DB_ENV=/etc/vexor/db.env
MIG_DIR=/usr/share/vexor-logs/migrations
[ -d "$MIG_DIR" ] || exit 0
[ -f "$DB_ENV" ] || { echo "vexor-logs: $DB_ENV missing - skipping migrations"; exit 0; }

# Extract user/pass/db from VEXOR_DB_URL=mysql+pymysql://user:pass@host/db
URL=$(grep -E "^VEXOR_DB_URL=" "$DB_ENV" | head -1 | cut -d= -f2-)
[ -z "$URL" ] && exit 0
USER=$(echo "$URL" | sed -E "s|.*://([^:]+):.*|\1|")
PASS=$(echo "$URL" | sed -E "s|.*://[^:]+:([^@]+)@.*|\1|")
HOST=$(echo "$URL" | sed -E "s|.*@([^/:]+).*|\1|")
DB=$(  echo "$URL" | sed -E "s|.*/([^?]+).*|\1|")

for f in "$MIG_DIR"/*.sql; do
  [ -f "$f" ] || continue
  echo "vexor-logs: applying $(basename "$f")"
  if ! mysql -u"$USER" -p"$PASS" -h"$HOST" "$DB" < "$f" 2>&1; then
    echo "vexor-logs: WARNING migration $(basename "$f") returned non-zero - continuing"
  fi
done

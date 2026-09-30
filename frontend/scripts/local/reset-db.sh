#!/usr/bin/env bash
# Drops and recreates a local database, re-runs Supabase Auth and app migrations.
# Local development only. Usage: scripts/local/reset-db.sh [database-name]
set -euo pipefail
source "$(dirname "$0")/env.sh"
DB_NAME="${1:-ten}"
case "$DB_NAME" in *prod*|*demo*) echo "Refusing to reset $DB_NAME"; exit 1;; esac
"$ROOT_DIR/scripts/local/stop.sh" >/dev/null 2>&1 || true
"$ROOT_DIR/scripts/local/start.sh" "$DB_NAME" >/dev/null
for pidfile in "$LOCAL_DIR"/run/auth-*.pid "$LOCAL_DIR"/run/gateway-*.pid; do
  if [ -f "$pidfile" ]; then kill "$(cat "$pidfile")" 2>/dev/null || true; rm -f "$pidfile"; fi
done
dropdb --if-exists --force "$DB_NAME"
rm -rf "$LOCAL_DIR/storage"
"$ROOT_DIR/scripts/local/start.sh" "$DB_NAME"
DATABASE_URL="postgres://postgres@127.0.0.1:$PG_PORT/$DB_NAME" node --import tsx "$ROOT_DIR/scripts/migrate.ts"

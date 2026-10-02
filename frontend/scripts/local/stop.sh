#!/usr/bin/env bash
# Stops everything started by scripts/local/start.sh.
set -uo pipefail
source "$(dirname "$0")/env.sh"
for pidfile in "$LOCAL_DIR"/run/*.pid; do
  [ -f "$pidfile" ] || continue
  kill "$(cat "$pidfile")" 2>/dev/null
  rm -f "$pidfile"
done
${PG_AS[@]+"${PG_AS[@]}"} "$PG_BIN/pg_ctl" -D "$PG_DATA" -m fast stop >/dev/null 2>&1 && echo "Postgres stopped"
echo "Local stack stopped"

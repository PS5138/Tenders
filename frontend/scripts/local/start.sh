#!/usr/bin/env bash
# Starts the native local stack: Postgres, Supabase Auth and the /auth/v1 gateway.
# Usage: scripts/local/start.sh [database-name] [auth-port] [gateway-port]
set -euo pipefail
source "$(dirname "$0")/env.sh"
DB_NAME="${1:-ten}"
AUTH_PORT="${2:-9999}"
GATEWAY_PORT="${3:-54321}"
ENV_FILE="$ROOT_DIR/.env.local"
[ -f "$ENV_FILE" ] || node "$ROOT_DIR/scripts/local/write-env.mjs"
JWT_SECRET="$(grep '^LOCAL_JWT_SECRET=' "$ENV_FILE" | cut -d= -f2-)"
SITE_URL="$(grep '^APP_URL=' "$ENV_FILE" | cut -d= -f2- || echo http://localhost:3000)"

mkdir -p "$LOCAL_DIR/logs" "$PG_SOCKET_DIR" "$PG_DATA"
if [ "${#PG_AS[@]}" -gt 0 ]; then chown postgres "$PG_DATA" "$PG_SOCKET_DIR"; touch "$LOCAL_DIR/logs/postgres.log"; chown postgres "$LOCAL_DIR/logs/postgres.log"; fi
[ -x "$AUTH_BIN" ] || "$ROOT_DIR/scripts/local/install-auth.sh"

if [ ! -f "$PG_DATA/PG_VERSION" ]; then
  "${PG_AS[@]}" "$PG_BIN/initdb" -D "$PG_DATA" -U postgres --auth=trust --encoding=UTF8 >/dev/null
fi
if ! "${PG_AS[@]}" "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
  "${PG_AS[@]}" "$PG_BIN/pg_ctl" -D "$PG_DATA" -o "-p $PG_PORT -k $PG_SOCKET_DIR -c listen_addresses=127.0.0.1" \
    -l "$LOCAL_DIR/logs/postgres.log" -w start >/dev/null
fi
psql -q -d postgres -v ON_ERROR_STOP=1 -f "$ROOT_DIR/scripts/local/bootstrap.sql"
if ! psql -tAq -d postgres -c "select 1 from pg_database where datname='$DB_NAME'" | grep -q 1; then
  createdb "$DB_NAME"
fi
psql -q -d "$DB_NAME" -v ON_ERROR_STOP=1 -f "$ROOT_DIR/scripts/local/bootstrap.sql"

pidfile="$LOCAL_DIR/run/auth-$AUTH_PORT.pid"
if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  echo "Supabase Auth already running on :$AUTH_PORT"
else
  (
    cd "$LOCAL_DIR/auth"
    auth_env=(GOTRUE_API_HOST=127.0.0.1 PORT="$AUTH_PORT"
      API_EXTERNAL_URL="http://127.0.0.1:$GATEWAY_PORT/auth/v1"
      GOTRUE_DB_DRIVER=postgres
      DATABASE_URL="postgres://supabase_auth_admin:local-auth-admin@127.0.0.1:$PG_PORT/$DB_NAME"
      GOTRUE_SITE_URL="$SITE_URL" GOTRUE_URI_ALLOW_LIST="$SITE_URL/**"
      GOTRUE_JWT_SECRET="$JWT_SECRET" GOTRUE_JWT_EXP=3600 GOTRUE_JWT_AUD=authenticated
      GOTRUE_JWT_ADMIN_ROLES=service_role
      GOTRUE_DISABLE_SIGNUP=true GOTRUE_EXTERNAL_EMAIL_ENABLED=true GOTRUE_MAILER_AUTOCONFIRM=true
      GOTRUE_LOG_LEVEL=warn)
    env "${auth_env[@]}" "$AUTH_BIN" migrate >"$LOCAL_DIR/logs/auth-$AUTH_PORT-migrate.log" 2>&1 \
      || { echo "Supabase Auth migrations failed; see $LOCAL_DIR/logs/auth-$AUTH_PORT-migrate.log"; exit 1; }
    env "${auth_env[@]}" nohup "$AUTH_BIN" serve >"$LOCAL_DIR/logs/auth-$AUTH_PORT.log" 2>&1 &
    echo $! >"$pidfile"
  )
fi

gwpid="$LOCAL_DIR/run/gateway-$GATEWAY_PORT.pid"
if [ -f "$gwpid" ] && kill -0 "$(cat "$gwpid")" 2>/dev/null; then
  echo "Gateway already running on :$GATEWAY_PORT"
else
  LOCAL_GATEWAY_PORT="$GATEWAY_PORT" LOCAL_AUTH_PORT="$AUTH_PORT" \
    nohup node "$ROOT_DIR/scripts/local/gateway.mjs" >"$LOCAL_DIR/logs/gateway-$GATEWAY_PORT.log" 2>&1 &
  echo $! >"$gwpid"
fi

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$GATEWAY_PORT/auth/v1/health" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$GATEWAY_PORT/auth/v1/health" >/dev/null \
  || { echo "Supabase Auth did not start; see $LOCAL_DIR/logs/auth-$AUTH_PORT.log"; exit 1; }
echo "Local stack ready: postgres :$PG_PORT db=$DB_NAME, auth :$AUTH_PORT, gateway :$GATEWAY_PORT"

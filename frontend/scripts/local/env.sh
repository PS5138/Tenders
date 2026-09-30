# Shared settings for the native local stack (Postgres + Supabase Auth binary).
# Sourced by the other scripts in this folder. Override any value by exporting it first.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_DIR="${LOCAL_DIR:-$ROOT_DIR/.local}"
PG_BIN="${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
[ -x "$PG_BIN/initdb" ] || PG_BIN="$(dirname "$(command -v initdb 2>/dev/null || echo /usr/bin/initdb)")"
PG_PORT="${PG_PORT:-54322}"
PG_DATA="$LOCAL_DIR/pg"
PG_SOCKET_DIR="$LOCAL_DIR/run"
AUTH_VERSION="${AUTH_VERSION:-v2.180.0}"
AUTH_BIN="$LOCAL_DIR/bin/auth"
export PGHOST=127.0.0.1 PGPORT="$PG_PORT" PGUSER=postgres
# Postgres refuses to run as root (e.g. in containers); run its server tools as
# the postgres system user in that case.
if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
  PG_AS=(runuser -u postgres --)
else
  PG_AS=()
fi
export PGOPTIONS="-c client_min_messages=warning"

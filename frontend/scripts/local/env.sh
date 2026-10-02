# Shared settings for the native local stack (Postgres + Supabase Auth binary).
# Sourced by the other scripts in this folder. Override any value by exporting it first.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_DIR="$(dirname "$ROOT_DIR")"
LOCAL_DIR="${LOCAL_DIR:-$ROOT_DIR/.local}"
# Postgres server tools, in order: an explicit PG_BIN, a Debian/Ubuntu package, an initdb on
# PATH, then the Postgres 16 binaries bundled with the backend's `pgserver` package in the
# repository's .venv (the same server backend/scripts/dev_db.py runs; it ships only the
# `vector` extension, which is why bootstrap.sql and the migrations tolerate a missing pgcrypto).
# (`|| true`: callers source this under `set -eo pipefail`, and the glob misses on most machines.)
PG_BIN="${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)}"
[ -x "$PG_BIN/initdb" ] || PG_BIN="$(dirname "$(command -v initdb 2>/dev/null || echo /usr/bin/initdb)")"
if [ ! -x "$PG_BIN/initdb" ] && [ -x "$REPO_DIR/.venv/bin/python" ]; then
  PG_BIN="$("$REPO_DIR/.venv/bin/python" -c 'import os, pgserver; print(os.path.join(os.path.dirname(pgserver.__file__), "pginstall", "bin"))' 2>/dev/null || true)"
fi
if [ ! -x "$PG_BIN/initdb" ]; then
  echo "No PostgreSQL server found. Install PostgreSQL 15+ and set PG_BIN to its bin directory," \
    "or create the backend virtualenv with the pgserver package at $REPO_DIR/.venv (see backend/README.md)." >&2
  exit 1
fi
# start.sh and reset-db.sh call psql, createdb and dropdb by name.
export PATH="$PG_BIN:$PATH"
PG_PORT="${PG_PORT:-54322}"
PG_DATA="$LOCAL_DIR/pg"
PG_SOCKET_DIR="$LOCAL_DIR/run"
# The current stable Supabase Auth release. Stable releases from v2.195.0 publish a
# darwin-arm64 asset next to the Linux x86 one, so one default serves both platforms
# install-auth.sh supports; the Compose stack still pins supabase/gotrue:v2.180.0
# (docker-compose.yml), whose release has no macOS asset.
AUTH_VERSION="${AUTH_VERSION:-v2.197.0}"
AUTH_BIN="$LOCAL_DIR/bin/auth"
export PGHOST=127.0.0.1 PGPORT="$PG_PORT" PGUSER=postgres
# Postgres refuses to run as root (e.g. in containers); run its server tools as
# the postgres system user in that case.
# Callers expand it as ${PG_AS[@]+"${PG_AS[@]}"}: macOS ships bash 3.2, where "${PG_AS[@]}" on an
# empty array is an unbound-variable error under set -u.
if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
  PG_AS=(runuser -u postgres --)
else
  PG_AS=()
fi
export PGOPTIONS="-c client_min_messages=warning"

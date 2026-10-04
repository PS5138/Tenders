#!/usr/bin/env bash
# Native development stack for the backend on a machine without Docker: the pgserver Postgres
# from dev_db.py (migrated and seeded), the worker in the background and uvicorn in the
# foreground. Defaults to the fake providers with SYNTHETIC_DEMO=true and no service secret,
# which is what the frontend's native .env.local (BACKEND_SERVICE_SECRET empty) expects.
#
#   backend/scripts/dev_up.sh            # API on http://127.0.0.1:8000, Swagger UI at /docs
#   PORT=8010 backend/scripts/dev_up.sh  # another port
#
# Stop with Ctrl-C (the worker is stopped too); the database keeps running until
# `.venv/bin/python backend/scripts/dev_db.py --stop`.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no virtualenv at $ROOT/.venv; see backend/README.md" >&2; exit 1; }

export LLM_PROVIDER="${LLM_PROVIDER:-fake}"
export EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER:-fake}"
export SYNTHETIC_DEMO="${SYNTHETIC_DEMO:-true}"
export STORAGE_PATH="${STORAGE_PATH:-$ROOT/storage}"
unset SERVICE_SECRET
mkdir -p "$STORAGE_PATH"

url_line="$("$PY" backend/scripts/dev_db.py --migrate --detach | grep '^DATABASE_URL=')"
export "${url_line?dev_db.py printed no DATABASE_URL}"
echo "$url_line" >&2

cd "$ROOT/backend"
"$PY" -m app.worker >"$STORAGE_PATH/dev-worker.log" 2>&1 &
WORKER=$!
echo "worker pid $WORKER (log: $STORAGE_PATH/dev-worker.log)" >&2
echo "providers: LLM_PROVIDER=$LLM_PROVIDER EMBEDDING_PROVIDER=$EMBEDDING_PROVIDER SYNTHETIC_DEMO=$SYNTHETIC_DEMO" >&2

# Not exec: the shell must outlive uvicorn so the trap stops the worker too, whether the stack
# is ended by Ctrl-C or by a SIGTERM to this script (as a launcher sends).
"$ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "${PORT:-8000}" --workers 1 &
API=$!
trap 'kill "$API" "$WORKER" 2>/dev/null || true' EXIT INT TERM
wait "$API"

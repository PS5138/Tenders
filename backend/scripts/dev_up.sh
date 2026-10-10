#!/usr/bin/env bash
# Native development stack for the backend on a machine without Docker: the pgserver Postgres
# from dev_db.py (migrated and seeded), the worker in the background and uvicorn in the
# foreground, with no service secret, which is what the frontend's native .env.local
# (BACKEND_SERVICE_SECRET empty) expects.
#
# Providers come from the environment, then the repo-root .env (app.config reads it itself), so
# a filled-in .env is all a live run needs. With no provider named anywhere, or a live provider
# named without its key, the backend runs on the keyless synthetic stand-ins instead and says so.
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

# A setting's value: the environment first, then the last NAME= line of the repo-root .env.
setting() {
  local value="${!1:-}"
  if [ -z "$value" ] && [ -f "$ROOT/.env" ]; then
    value="$(sed -n "s/^$1=//p" "$ROOT/.env" | tail -1 | tr -d "\r\"'" | xargs)"
  fi
  printf '%s' "$value"
}
LLM="$(setting LLM_PROVIDER)"
EMB="$(setting EMBEDDING_PROVIDER)"
missing=""
[ "$LLM" = anthropic ] && [ -z "$(setting ANTHROPIC_API_KEY)" ] && missing="$missing ANTHROPIC_API_KEY"
[ "$EMB" = openai ] && [ -z "$(setting OPENAI_API_KEY)" ] && missing="$missing OPENAI_API_KEY"
[ "$EMB" = voyage ] && [ -z "$(setting VOYAGE_API_KEY)" ] && missing="$missing VOYAGE_API_KEY"
if [ -z "$LLM" ] || [ -z "$EMB" ] || [ -n "$missing" ]; then
  if [ -n "$missing" ]; then
    echo "warning: .env selects live providers but these keys are empty:$missing." >&2
    echo "warning: running on the synthetic stand-ins until they are filled in." >&2
  fi
  export LLM_PROVIDER=fake EMBEDDING_PROVIDER=fake SYNTHETIC_DEMO=true
fi
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
echo "providers: LLM_PROVIDER=$(setting LLM_PROVIDER) EMBEDDING_PROVIDER=$(setting EMBEDDING_PROVIDER) SYNTHETIC_DEMO=$(setting SYNTHETIC_DEMO)" >&2

# Not exec: the shell must outlive uvicorn so the trap stops the worker too, whether the stack
# is ended by Ctrl-C or by a SIGTERM to this script (as a launcher sends).
"$ROOT/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "${PORT:-8000}" --workers 1 &
API=$!
trap 'kill "$API" "$WORKER" 2>/dev/null || true' EXIT INT TERM
wait "$API"

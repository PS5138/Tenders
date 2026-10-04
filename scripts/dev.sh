#!/usr/bin/env bash
# One command for the whole native stack: `npm run dev` from the repository root.
#
#   1. backend: Postgres (:54329), the API (:8000) and the worker, via backend/scripts/dev_up.sh
#   2. frontend services: Postgres (:54322), Supabase Auth (:9999) and its gateway (:54321)
#   3. migrations, development accounts and the synthetic seed (all safe to repeat)
#   4. the Next.js app on http://localhost:3000, in the foreground
#
# One Ctrl-C stops everything this script started. The first run installs what is missing (the
# Python virtualenv, node_modules, the Auth binary) and takes a few minutes; later runs take
# seconds. Fake providers and SYNTHETIC_DEMO=true by default, as in dev_up.sh; export
# LLM_PROVIDER, EMBEDDING_PROVIDER, SYNTHETIC_DEMO and the keys before running to use live models.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOGS="$ROOT/storage"
mkdir -p "$LOGS"
PNPM_VERSION="$(sed -n 's/.*"packageManager": *"pnpm@\([^"]*\)".*/\1/p' frontend/package.json)"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }
pnpm_() {
  if command -v pnpm >/dev/null 2>&1; then pnpm "$@"; else npx -y "pnpm@$PNPM_VERSION" "$@"; fi
}
listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
api_healthy() { curl -sf -m 2 http://127.0.0.1:8000/health >/dev/null 2>&1; }

BACKEND_PID=""
SEED_PID=""
NEXT_PID=""
STARTED_BACKEND=false
cleanup() {
  trap - EXIT INT TERM
  say "Stopping"
  [ -n "$NEXT_PID" ] && kill -TERM "$NEXT_PID" 2>/dev/null && wait "$NEXT_PID" 2>/dev/null || true
  [ -n "$SEED_PID" ] && kill "$SEED_PID" 2>/dev/null || true
  if $STARTED_BACKEND; then
    # dev_up.sh traps TERM and stops the API and the worker with it.
    [ -n "$BACKEND_PID" ] && kill -TERM "$BACKEND_PID" 2>/dev/null && wait "$BACKEND_PID" 2>/dev/null || true
    "$ROOT/.venv/bin/python" "$ROOT/backend/scripts/dev_db.py" --stop >/dev/null 2>&1 || true
  fi
  (cd "$ROOT/frontend" && scripts/local/stop.sh >/dev/null 2>&1) || true
  echo "Everything started by npm run dev has stopped."
}
trap cleanup EXIT INT TERM

# --- Prerequisites ------------------------------------------------------------------------------
command -v node >/dev/null 2>&1 || fail "Node 22 is required (https://nodejs.org or nvm)."
command -v curl >/dev/null 2>&1 || fail "curl is required."
if [ ! -x .venv/bin/python ]; then
  command -v uv >/dev/null 2>&1 || fail "No .venv found and uv is not installed. Install uv (https://docs.astral.sh/uv/) and rerun."
  say "Creating the Python virtualenv (first run only)"
  uv venv .venv --python 3.12
  uv pip install --python .venv/bin/python -r pyproject.toml --extra dev
fi
if [ ! -d frontend/node_modules ]; then
  say "Installing frontend dependencies (first run only)"
  (cd frontend && pnpm_ install --frozen-lockfile)
fi
listening 3000 && fail "Port 3000 is already in use. Stop whatever is running there (another 'pnpm dev'?) and rerun."

# --- 1. Backend ---------------------------------------------------------------------------------
if api_healthy; then
  say "Backend already running on :8000; using it (it is left running on exit)"
else
  listening 8000 && fail "Port 8000 is in use by something that is not this API."
  say "Starting the backend (API, worker and database); log: storage/dev-api.log"
  backend/scripts/dev_up.sh >"$LOGS/dev-api.log" 2>&1 &
  BACKEND_PID=$!
  STARTED_BACKEND=true
  for _ in $(seq 1 120); do
    api_healthy && break
    kill -0 "$BACKEND_PID" 2>/dev/null || { tail -20 "$LOGS/dev-api.log" >&2; fail "The backend did not start; see storage/dev-api.log."; }
    sleep 1
  done
  api_healthy || { tail -20 "$LOGS/dev-api.log" >&2; fail "The backend did not become healthy within two minutes."; }
fi

# --- 2 and 3. Frontend services, migrations, accounts, seed --------------------------------------
cd frontend
say "Starting the frontend database and login service"
pnpm_ --silent local:start
say "Applying migrations and creating the development accounts"
pnpm_ --silent db:migrate
pnpm_ --silent db:provision
# The seed waits for the worker to ingest the documents, so it runs beside the app rather than
# ahead of it; the library shows Queued or Parsing until each document is ready.
say "Seeding the synthetic library and sample tender in the background; log: storage/dev-seed.log"
pnpm_ --silent db:seed-backend >"$LOGS/dev-seed.log" 2>&1 &
SEED_PID=$!

# --- 4. The app ---------------------------------------------------------------------------------
cat <<'EOF'

  Ten is starting on  http://localhost:3000
  Sign in as          puru@example-health.test   password: ten-dev-only
  (other accounts are listed in README.md)

  Logs: storage/dev-api.log (API), storage/dev-worker.log (worker), storage/dev-seed.log (seed)
  Press Ctrl-C once to stop everything.

EOF
# In the background with a wait, not in the foreground: the shell runs its trap as soon as a
# signal arrives instead of after Next exits, so a launcher's SIGTERM stops everything too.
node_modules/.bin/next dev &
NEXT_PID=$!
wait "$NEXT_PID"

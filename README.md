# Ten

Ten joins an invite-only Next.js frontend to a private FastAPI service. The backend owns documents, tenders, questions, answers, review and exports. The frontend owns accounts, business membership, notifications and confirmed buyer-form locations.

## Local synthetic stack

Install Docker with Compose v2, then run from this directory:

```sh
docker compose up --build -d
docker compose logs -f frontend-provision frontend-seed web api worker
```

If this machine ran the earlier single-app Ten demo, run `docker compose down -v --remove-orphans` once before the first start. Both stacks use the Compose project name `ten`, and the old `ten_storage` volume belongs to a different user, so backend uploads would fail. This erases only the old demo's synthetic data.

Open `http://localhost:3000` and sign in with any development account below (password `ten-dev-only`). In this local stack, the account menu (top right) can switch between them without signing out; the switch exists only when `APP_MODE=development` and `DEMO_USER_SWITCH=true`. These accounts must never be used in a hosted demonstration.

| Account | Business | Access |
|---|---|---|
| `valerie@example-health.test` | Example Health | Admin |
| `puru@example-health.test` | Example Health | Admin |
| `roger@example-health.test` | Example Health | Admin |
| `sam@example-health.test` (bid writer) | Example Health | Member |
| `alex@example-health.test` (clinical safety SME) | Example Health | Member |
| `jordan@example-health.test` (information governance SME) | Example Health | Member |
| `olive@riverside-medical.test` | Riverside Medical, a separate business | Admin |
| `riley@riverside-medical.test` | Riverside Medical | Member |

Only port 3000 is published. Auth, both databases, the API and worker stay on the Compose network. The API requires the shared service secret set by Compose. The default secret is local-development-only. The first build downloads Docling and CPU model dependencies and can take time.

The stack defaults to fake providers with `SYNTHETIC_DEMO=true`, which enables deterministic processing of the supplied `eval/data` files. In synthetic mode both upload routes are gated, `POST /documents` and `POST /tenders/{id}/documents`: bytes that are not byte-for-byte one of the `eval/data` files are refused with 422. It is a plumbing demonstration, not evidence of model quality. API and worker must use the same configuration and storage volume. To run the same stack on live models, see [Live providers](#live-providers).

Two one-shot services load the synthetic data. `frontend-provision` creates the development accounts, the two businesses and their mapped backend organisations; `web` waits for it, because sign-up is disabled and the login page needs accounts to exist. `frontend-seed` then uploads and confirms four synthetic library sources and uploads a question pack, and runs alongside `web` rather than ahead of it: the app is usable as soon as `web` is up, and the library shows Queued, Parsing or Failed badges on the documents while the worker is still processing them. The worker extracts and triages the question pack once it arrives.

Recovery: `frontend-seed` is safe to rerun at any time with `docker compose run --rm frontend-seed`. It skips every library source whose filename already exists for the business and is queued, processing or ready, uploads a source that reads Failed again (the earlier Failed row stays in the library as a record), continues past a document that fails (logged as a warning), waits for the worker for at most five minutes in total (`SEED_WAIT_MS` to change it) and exits successfully once the question pack is in place. If a library source reads Failed in the library, or coverage on the sample tender reads as new because the sources were not ready when the pack was triaged, inspect the worker log, fix the cause, rerun the seed and use "Re-check coverage" on the tender. Data is persistent; do not delete volumes to troubleshoot unless you intend to erase the local data.

If the app refuses to save anything with a message naming two addresses, the browser is open at an address other than `APP_URL` (default `http://localhost:3000`). The app shows the same notice at the top of every page before you try. Open the app at the configured address, or set `APP_URL` to the address you use (for example `http://127.0.0.1:3000` or a LAN name) and restart `docker compose up -d`. Uploads larger than `MAX_UPLOAD_BYTES` (25 MiB by default) are refused by the frontend with a 413 before reaching the API.

## Live providers

The same stack on live models, with the `docker-compose.live.yml` override layered over the base file:

```sh
docker compose -f docker-compose.yml -f docker-compose.live.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.live.yml logs -f frontend-provision frontend-seed web api worker
```

Every later `docker compose` command for this stack (`logs`, `run`, `down`) needs the same two `-f` flags, or `export COMPOSE_FILE=docker-compose.yml:docker-compose.live.yml` once.

The override sets `LLM_PROVIDER=anthropic`, `EMBEDDING_PROVIDER=openai` (or `voyage` when `EMBEDDING_PROVIDER=voyage` is exported) and `SYNTHETIC_DEMO=false` on `migrate`, `api` and `worker`, and passes `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `VOYAGE_API_KEY` through from the shell or the repo-root `.env` (copy `.env.example`, which carries the live defaults, and fill in the keys). Keys needed: `ANTHROPIC_API_KEY` always, plus `OPENAI_API_KEY` for `openai` embeddings or `VOYAGE_API_KEY` for `voyage`.

The API and the worker check the three variables at start-up and refuse to run until they agree: `SYNTHETIC_DEMO=true` requires both providers to be `fake`, and a live provider requires its key. The refusal is a plain message in `docker compose logs api` or `logs worker`, not a failing first job; compose restarts the container until the configuration is fixed, and `web` does not start until `api` is healthy. The base file pins `LLM_PROVIDER=fake`, `EMBEDDING_PROVIDER=fake` and `SYNTHETIC_DEMO=true` in `environment`, which wins over `env_file`, so a plain `docker compose up` is the synthetic stack whatever the repo-root `.env` says (the provider lines in `.env` govern native runs, and the keys still pass through to the override); live providers are reached only through `docker-compose.live.yml`, so the choice is explicit in the command and never depends on which file happens to be present.

`frontend-seed` then ingests the four synthetic library documents and the question pack through the live models (Docling parse, classification, pair extraction, embedding, question extraction and triage) while `web` is already serving. Expect the first run on live providers to take several minutes rather than the seconds the fake providers take, and to spend real tokens; the library shows the documents as Queued or Parsing until each one finishes. The seed waits at most five minutes in total for the worker, then uploads the question pack anyway and exits successfully; a document that fails is a logged warning, not a failed run. If a source reads Failed, read the worker log, fix the cause and rerun `docker compose -f docker-compose.yml -f docker-compose.live.yml run --rm frontend-seed`, which uploads the Failed source again, skips sources that are queued, processing or ready, and leaves the earlier Failed row in the library as a record.

The frontend stays in `APP_MODE=development` under this override. `frontend-provision` creates the published-password development accounts and, like `frontend-seed`, refuses to run in `APP_MODE=demo`, which is the hosted configuration in the [deployment runbook](frontend/docs/deploy.md) and provisions its first business with `pnpm business:create` instead. Live providers on this local stack are for exercising the pipeline on real models, not for a hosted demonstration.

## Code and checks

- [Frontend setup and tests](frontend/README.md)
- [Backend setup and API](backend/README.md)
- [Backend product behaviour](CLAUDE.md)
- [Deployment runbook](frontend/docs/deploy.md)

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r pyproject.toml --extra dev
.venv/bin/python -m pytest backend/tests -q -p no:warnings
.venv/bin/ruff check backend eval
cd frontend
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Backend changes made for the frontend integration (service secret, richer health, organisation provisioning, original-file download, synthetic demo mode, tender editing, archiving and deletion) are covered by `backend/tests/test_frontend_integration.py`.

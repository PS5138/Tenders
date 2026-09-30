# Ten

Ten joins an invite-only Next.js frontend to a private FastAPI service. The backend owns documents, tenders, questions, answers, review and exports. The frontend owns accounts, business membership, notifications and confirmed buyer-form locations.

## Local synthetic stack

Install Docker with Compose v2, then run from this directory:

```sh
docker compose up --build -d
docker compose logs -f frontend-seed web api worker
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

The stack defaults to fake providers with `SYNTHETIC_DEMO=true`, which enables deterministic processing of the supplied `eval/data` files. The synthetic mode rejects other upload bytes. It is a plumbing demonstration, not evidence of model quality. API and worker must use the same configuration and storage volume.

The seed creates two mapped organisations, confirms four synthetic library sources, and uploads a question pack. The worker extracts and triages it. If classification or seed processing fails, inspect the worker log and rerun `docker compose run --rm frontend-seed` after fixing the cause. Data is persistent; do not delete volumes to troubleshoot unless you intend to erase the local data.

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

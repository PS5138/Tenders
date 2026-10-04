# Ten frontend

Next.js 16.3.6 / React 19.2.8, using Puru’s FastAPI service as the sole tender-domain store. Read the bundled Next.js guides in `node_modules/next/dist/docs` before changing framework APIs.

## Run

The supported combined local entry point is `docker compose up --build -d` from the repository root. See the [root README](../README.md). There is no frontend worker or separate frontend Compose stack.

### Native development (Linux x86_64 and macOS on Apple silicon)

The quickest way is `npm run dev` from the repository root, which runs every step below in order and stops everything on one Ctrl-C (see the [root README](../README.md#quick-start)). The steps are listed here for running or restarting the parts separately.

Nothing here needs Docker, Homebrew or admin rights. Prerequisites:

- Node 22 with pnpm. Any Node 22 works; the team's Macs keep one under `~/.local`, so run `export PATH="$HOME/.local/node/current/bin:$PATH"` first.
- The backend virtualenv at the repository root (`.venv`, see `backend/README.md`). Its `pgserver` package bundles Postgres 16, and `scripts/local/env.sh` falls back to those binaries when no system PostgreSQL is found, so the frontend database needs nothing else installed. That build ships only the `vector` extension; nothing in our SQL calls `pgcrypto` functions, so `bootstrap.sql` and the squashed migration only raise a notice when the extension is missing.
- `curl` and `tar` for the one-time Supabase Auth download (`scripts/local/install-auth.sh` picks the release asset for the platform and exits with `unsupported platform: use Docker Compose` elsewhere).

Start the API and the worker first, in their own terminal, from the repository root:

```sh
backend/scripts/dev_up.sh   # pgserver Postgres on :54329 (migrated and seeded), the worker in the background, uvicorn on http://127.0.0.1:8000
```

It uses the fake providers with `SYNTHETIC_DEMO=true` and no service secret, which is what the generated `.env.local` expects (`BACKEND_URL=http://127.0.0.1:8000`, `BACKEND_SERVICE_SECRET` empty). Then, from `frontend/`, in this order:

```sh
pnpm install --frozen-lockfile
pnpm local:start       # Postgres :54322 (database `ten`), Supabase Auth :9999 and the /auth/v1 gateway :54321; writes .env.local with local-only secrets on first run
pnpm db:migrate
pnpm db:provision      # development/test accounts, businesses and backend organisations (needs the API)
pnpm db:seed-backend   # synthetic library documents and question pack from ../eval/data (BACKEND_FIXTURES_DIR overrides); needs db:provision first
pnpm dev
```

`pnpm local:start` is idempotent: it reuses the cluster, the Auth binary and `.env.local` (`node scripts/local/write-env.mjs --force` regenerates the secrets). Everything it creates lives under `frontend/.local/`: the cluster in `pg/`, the Auth binary and its migrations in `bin/` and `auth/`, pid files in `run/`, and logs in `logs/` (`postgres.log`, `auth-9999-migrate.log`, `auth-9999.log`, `gateway-54321.log`). The backend side logs the worker to `storage/dev-worker.log`, uvicorn to its terminal and its Postgres to `.devdb/server.log`. `bash -c 'source scripts/local/env.sh && psql -d ten'` opens the sidecar database with the same binaries. `pnpm db:reset` drops and recreates `ten` and re-runs both migration sets.

To stop: `pnpm local:stop` ends Postgres, Auth and the gateway; Ctrl-C in the `dev_up.sh` terminal ends the API and the worker; `.venv/bin/python backend/scripts/dev_db.py --stop` ends the backend Postgres.

Natively, Supabase Auth is the `v2.197.0` release binary (`AUTH_VERSION` in `scripts/local/env.sh`); stable releases from `v2.195.0` publish a macOS arm64 asset as well as the Linux x86 one. The Compose stack runs the `supabase/gotrue:v2.180.0` image, whose release has no macOS asset. Both apply the same `auth` schema migrations; override `AUTH_VERSION` to pin another release that has an asset for your platform.

The squashed frontend migration is for a fresh database. Nothing was deployed under the old schema. Do not apply it over an old experimental frontend database; retain a backup and create a new local database instead.

## Boundaries

The browser calls `/api/w/:workspaceId/backend/...`. Every request verifies the session, active membership and business-to-organisation mapping. Routes, methods and query parameters are explicitly allow-listed. The server injects actor, organisation and service-secret headers; browser-supplied identity is refused. Mutations require the exact app origin and `X-Ten-Request: 1`.

Uploads and NDJSON streams bypass Next’s proxy matcher. The bridge passes request and response streams directly, disables compression and preserves download filenames. Only small question metadata/comment requests are inspected to validate assignees and emit notifications. Generation reconnects by polling persisted results, never automatically repeating the POST.

The sidecar schema contains identity, invitations, membership history, notifications, reviewers, comment anchors and confirmed form locations. It does not store tender, document, question, answer or thread content. Anchored comments are mirrored into the backend record: every new thread or reply is first written through the API’s comments endpoint, which stores the comment and its `comment_added` event with the actor, as the quoted words, the comment and a `[thread <id>]` reference; only when that succeeds does the sidecar keep the anchor and the returned backend comment id, and a failed backend write saves nothing. Resolving and reopening threads stays in the sidecar. Comments cannot be removed from the backend record: an author can hide their own message from the Comments tab, which filters it from reads but deletes nothing. Form exports re-read approved current answers and originals from the API, run its export gate, and refuse missing, occupied or duplicate target cells. Office ZIP helpers remain solely for filling forms.

Notifications cover assignments, status changes for the assignee, and comments for the assignee or named `@Display Name` mentions made through Ten. Backend changes made outside Ten do not generate sidecar notifications. Delivery is best effort across the two databases; failures are logged without misreporting a saved backend change. Deleted-question notifications and form mappings are hidden on reads. My reviews and team assignment counts are read live from the backend. Display names must be unique within each business because backend authorship and assignment use names.

## Operators

```sh
pnpm business:create --name "Example Health" --email person@example.com --display-name "Name"
pnpm business:provision-backend --workspace <workspace-uuid>  # retry a partial bootstrap
pnpm business:link-backend --workspace <workspace-uuid> --organisation <existing-org-uuid>
pnpm user:reset-link --email person@example.com
```

Only operator scripts can write `backend_org_id`. Existing mappings cannot be silently reassigned. If organisation provisioning fails after business creation, retry with the printed business UUID, then generate a fresh invitation/reset link if necessary.

## Verification

```sh
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm backend:types             # fetches the configured API OpenAPI schema
pnpm backend:types --snapshot  # regenerates from the committed snapshot
```

Joined integration tests create and drop a temporary frontend database, exercise real PostgreSQL RLS and real API/worker calls, and stub only session verification. Use disposable databases with synthetic providers and a running worker:

```sh
BRIDGE_TEST_FRONTEND_DATABASE_URL=postgres://postgres@127.0.0.1:54328/postgres \
BRIDGE_TEST_DATABASE_URL=postgres://postgres@127.0.0.1:54329/tenders_dev \
BACKEND_URL=http://127.0.0.1:8000 \
BRIDGE_TEST_ALLOW_SYNTHETIC_WRITES=1 pnpm test:integration
```

Set `BACKEND_SERVICE_SECRET` too when the local API requires it. The backend database must have its migrations and standard seed. The tests leave synthetic backend rows in that disposable database. They also write `ten-filled-synthetic-form.xlsx` to the system temporary folder for Office rendering inspection.

Against the root Compose stack, `pnpm test:e2e` runs the signed-in browser journeys: two-person drafting and review, the assistant and buyer-form filling, reviewers and comments, invitations, the test-user switch, tender management, the board, business isolation and four viewport sizes.

After a production build, `pnpm test:ui` runs 16 rendering checks with the backend’s synthetic fixture, stubbed navigation and API snapshots. It tests four screens at four widths, including source popovers. Set `PW_CHROMIUM_PATH` for your browser binary (defaults to macOS Chrome). This harness does not verify sign-in or mutations.

Backend-domain correctness belongs to the backend suite; the joined integration tests cover the boundary between the two.

# Ten frontend

Next.js 16.3.6 / React 19.2.8, using Puru’s FastAPI service as the sole tender-domain store. Read the bundled Next.js guides in `node_modules/next/dist/docs` before changing framework APIs.

## Run

The supported combined local entry point is `docker compose up --build -d` from the repository root. See the [root README](../README.md). There is no frontend worker or separate frontend Compose stack.

For native development, copy `.env.example` to `.env.local` and provide a running Supabase Auth service, frontend PostgreSQL database, private backend API and Python worker. `pnpm local:start` supplies Auth/Postgres on Linux x86_64 only; it does not start the Python service. On macOS use Docker or separately configured services.

```sh
pnpm install --frozen-lockfile
pnpm db:migrate
pnpm db:seed-backend   # development/test accounts and supplied synthetic data only
pnpm dev
```

The squashed frontend migration is for a fresh database. Nothing was deployed under the old schema. Do not apply it over an old experimental frontend database; retain a backup and create a new local database instead.

## Boundaries

The browser calls `/api/w/:workspaceId/backend/...`. Every request verifies the session, active membership and business-to-organisation mapping. Routes, methods and query parameters are explicitly allow-listed. The server injects actor, organisation and service-secret headers; browser-supplied identity is refused. Mutations require the exact app origin and `X-Ten-Request: 1`.

Uploads and NDJSON streams bypass Next’s proxy matcher. The bridge passes request and response streams directly, disables compression and preserves download filenames. Only small question metadata/comment requests are inspected to validate assignees and emit notifications. Generation reconnects by polling persisted results, never automatically repeating the POST.

The sidecar schema contains identity, invitations, membership history, notifications and confirmed form locations. It does not store tender, document, question, answer or thread content. Form exports re-read approved current answers and originals from the API, run its export gate, and refuse missing, occupied or duplicate target cells. Office ZIP helpers remain solely for filling forms.

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

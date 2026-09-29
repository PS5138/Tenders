# Deploying the joined system

Deployment has not been performed. Hosting, credentials, embedding-provider selection and use of real documents still require the team’s decision. London Supabase Auth and London private API/worker hosting are the proposed arrangement, not provisioned infrastructure.

## Services and data

- Next.js is the only public service, behind HTTPS.
- Supabase Auth remains invite-only. Set its Site URL and allowed auth redirect to the public `APP_URL`; keep self-sign-up disabled. Service-role credentials stay on the server.
- The frontend PostgreSQL connection needs permission to use the `authenticated` role for request-scoped RLS. Keep the `app` schema out of the public Data API. Use a fresh database and run `pnpm db:migrate` with migration credentials. Set `DATABASE_PREPARE=false` with a transaction pooler.
- FastAPI and the Python worker share backend PostgreSQL with pgvector and a persistent upload volume. Use a private network; do not publish the API or database ports. Keep a single API process because generation tasks live in its memory.
- Run backend Alembic migrations before starting API/worker, and the backend standard seed once. Do not run the frontend development-account seed in a hosted environment.

## Configuration

Frontend: `APP_MODE=demo`, public HTTPS `APP_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, private-origin `BACKEND_URL`, and a random `BACKEND_SERVICE_SECRET` of at least 32 characters.

Backend API and worker: `SERVICE_SECRET` equal to that frontend secret; `SYNTHETIC_DEMO=false`; `LLM_PROVIDER=anthropic`; approved embedding provider (`openai` or `voyage`) and provider keys; backend `DATABASE_URL`; persistent `STORAGE_PATH`. Configure model names through existing backend settings. Do not reuse the local Compose secret. Switching embedding dimensions/providers requires the backend’s migration and re-embedding process; do not change them on an existing index casually.

The frontend validates required configuration at boot. Demo mode checks private `/health` and refuses fake, synthetic or unreported providers, or an API that does not report service-secret enforcement. Health reports provider configuration, not successful live model calls. Verify actual generation separately.

The old `AI_PROVIDER`, `PURU_AI_*`, frontend `STORAGE_DRIVER`, signed-download and frontend-worker settings no longer apply. Files, pipeline jobs and exports live in the backend.

## Release procedure

1. Run both test suites and build the frontend image. Run the full joined Compose stack from a clean checkout with synthetic data, then all signed-in Playwright journeys with two users and two businesses.
2. Provision private hosted services, backups, credentials and the chosen embedding provider. Verify only Next.js is publicly reachable and API requests without the secret fail.
3. Apply migrations and seed the backend. Use `pnpm business:create` to create the first business and its backend organisation. Give the single-use link to its administrator. Never print secrets into build logs or commit environment files.
4. Exercise invite, reset, sign-in, membership revocation and a second business. Check cross-business tender/question/document/file/source access returns 404.
5. Run the live upload → classification → extraction/triage → draft → evidence review → approval → export/submit journey. Confirm NDJSON chunks arrive progressively through the actual hosting proxy. Set proxy buffering/compression off for this route and timeouts suitable for generation.
6. Obtain authorisation for two real packs, with one held out. Verify the release gates, including trace fidelity, conflicting and expired sources, and reviewer checks, and record the live results. Synthetic results cannot satisfy this gate.
7. Render downloaded DOCX/XLSX exports with Office/LibreOffice and inspect their content and layout. Confirm originals, formulas and unrelated cells are preserved.

## Operational limits

My reviews and team counts currently query every open tender; the optional cross-tender assignee endpoint remains a scaling improvement. Notification writes span two services and are best effort; monitor the `[notifications]` failure log. Display-name changes do not rename historic backend events or assignments: reassign open questions when a person changes their name. Names outside the Latin-1 HTTP-header range require an agreed backend actor encoding before those accounts use the bridge. The API/worker share one filesystem volume; scaling them to separate hosts requires shared storage.

If boot or provider validation fails, fix configuration and restart. Keep old database and storage snapshots for recovery; there is no migration path from the discarded, undeployed frontend tender schema.

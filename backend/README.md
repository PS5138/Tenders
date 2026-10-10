# Tenders backend

FastAPI API, a Postgres-backed worker, and the ingestion, retrieval, generation, review and export pipelines. The plan in `../CLAUDE.md` is the authority for behaviour; this file only says how to run things.

## Install

Every non-Docker command below runs from a `.venv` at the repo root (git-ignored). Create it with [uv](https://docs.astral.sh/uv/) and install the dependencies plus the `dev` extra from `pyproject.toml`; the project itself is never installed, since tests and the `python -m` commands find `app` through pyproject's `pythonpath` and the documented working directories, mirroring the Dockerfile's dependencies-only install:

```sh
cd <repo root>
uv venv .venv                      # Python 3.12 from .python-version
uv pip install --python .venv/bin/python -r pyproject.toml --extra dev
```

Without uv, `pip` cannot read `-r pyproject.toml`, so use the editable form instead: `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.

## Tests and lint

The suite runs entirely offline. It starts a throwaway Postgres 16 with pgvector through the `pgserver` package (bundled in its wheel, so the first run is slower only because it runs `initdb` on a throwaway cluster), applies the Alembic migrations from base to head, runs the seed, and forces the fake LLM and fake embedding providers.

```sh
cd <repo root>
.venv/bin/python -m pytest backend/tests -q -p no:warnings
.venv/bin/ruff check backend eval
```

Tests marked `integration` exercise another module end to end and skip when it cannot be imported; deselect them with `-m 'not integration'`.

## Run locally for development (no Docker)

You need a Postgres 16 with the `vector` extension. The quickest way is the bundled one the tests use, on a fixed port so it never collides with a Docker or system Postgres:

```sh
cd <repo root>
.venv/bin/python backend/scripts/dev_db.py --migrate --detach
# server started on 127.0.0.1:54329 (log: .devdb/server.log)
# migrated to head and seeded (...)
# DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:54329/tenders_dev
```

`dev_db.py` initialises a cluster under `.devdb/` (git-ignored) on first run, starts it on `127.0.0.1:54329` with trust authentication, creates the database and the `vector` extension, and prints the `DATABASE_URL`. `--migrate` also runs `alembic upgrade head` and the seed. Without `--detach` it waits and stops the server on Ctrl-C; with it, stop later with `--stop`. `--reset` wipes the data directory; `--port` and `--pgdata` change the defaults.

Then, in two shells from `backend/`, with the printed URL in the environment. A `.env` at the repo root works too (copy `.env.example`): `Settings` reads that file from any working directory, and exported variables override it. Both processes refuse to start when the selected provider has no key (`LLM_PROVIDER=anthropic` without `ANTHROPIC_API_KEY`, `EMBEDDING_PROVIDER=openai` without `OPENAI_API_KEY`), so pick the fake providers for a plumbing-only run:

```sh
export DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:54329/tenders_dev
export STORAGE_PATH=../storage
export LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=...      # or LLM_PROVIDER=fake for plumbing only
export EMBEDDING_PROVIDER=openai OPENAI_API_KEY=...      # or EMBEDDING_PROVIDER=fake

cd backend
../.venv/bin/uvicorn app.main:app --reload --port 8000   # shell 1: the API (one process)
../.venv/bin/python -m app.worker                        # shell 2: the job worker
```

If you skipped `--migrate`, run the migrations and seed yourself first: `../.venv/bin/alembic upgrade head && ../.venv/bin/python -m app.db.seed` from `backend/`.

The API runs LLM calls only where a request is waiting on them (the two streaming endpoints, the re-verification on edit-save and on accept-verbatim); everything else is a job on the worker, which registers the handlers listed in `app/worker.py` (`app.ingest.jobs`, `app.ingest.questions`, `app.retrieve.triage`, `app.generate.jobs`). Without the worker, uploads sit at `queued`. Interactive docs are at `http://localhost:8000/docs`.

## Run with compose

The backend has no compose file of its own: it runs inside the joined stack at the repository root, alongside the Next.js frontend, Supabase Auth and one Postgres holding both databases (see the [root README](../README.md)).

```sh
cd <repo root>
docker compose up --build -d                                                   # fake providers, SYNTHETIC_DEMO=true, no keys
docker compose -f docker-compose.yml -f docker-compose.live.yml up --build -d  # live providers; keys from the shell or .env
```

`db` is `pgvector/pgvector:pg16`; `migrate` builds the backend image (tagged `ten-backend-local`, from `backend/Dockerfile` with the repo root as context) and applies the migrations and the seed once; `api` (`uvicorn app.main:app --workers 1` on port 8000) and `worker` (`python -m app.worker`) start from that same image (`pull_policy: never`, so compose never looks for it on Docker Hub) after it completes and share the `storage` volume for uploaded files. Docling's PDF layout and TableFormer weights are downloaded into `DOCLING_ARTIFACTS_PATH` (`/opt/docling-models`) at image build time; nothing is fetched at start-up. A `.env` at the repo root is read by `migrate`, `api` and `worker` for provider keys and by compose for `${VAR:-default}` interpolation; the connection, storage and model-weight paths are pinned in `docker-compose.yml` so a local `.env` cannot redirect the containers.

Notes on the image, the ports and the secret:

- Only the frontend is published, on `3000`. The API and the database stay on the compose network: there is no host port for `api:8000` or `db:5432`, so the curl walkthrough below and the evaluation harness run against a native API (next section), not against the compose stack. To reach the compose API from the host for a one-off check, add a personal override file with a `ports:` entry on `api`; do not add one to the committed files.
- The API always runs with a service secret in compose: `SERVICE_SECRET` is set from `BACKEND_SERVICE_SECRET` in the repo-root `.env` (or the local-only default) and every request, including `/health` and `/openapi.json`, must carry `Authorization: Bearer <secret>`. The frontend sends it from its `BACKEND_SERVICE_SECRET`, which is why the two must be the same value.
- The three provider variables are checked at start-up (`app.config.check_provider_configuration`): `SYNTHETIC_DEMO=true` needs both providers `fake`, and a live provider needs its key. The base file defaults to the synthetic trio; `docker-compose.live.yml` pins the live trio on `migrate`, `api` and `worker` and passes the keys through. Change them together, and never run `api` and `worker` on different values.
- The image is CPU-only: `uv pip install --torch-backend cpu` takes torch and torchvision from the CPU wheel index, so the CUDA libraries PyPI's Linux build would pull in are never downloaded. It installs `libgl1` and `libglib2.0-0t64`, which opencv (via docling's OCR extra) needs even with OCR off; without them every PDF ingest fails at run time.
- The containers run as the unprivileged user `app` (uid 10001). `/data/storage` is created and owned in the image, so the `storage` volume inherits that ownership when compose first creates it. A volume created by an earlier root-running image needs `docker compose down -v` (or a one-off `docker compose run --rm --user root api chown 10001 /data/storage`), otherwise uploads fail to create their directories.
- The build context is the repo root trimmed by `/.dockerignore` (`.venv`, `.git`, `.devdb`, `storage`, `.env`, `frontend`, caches and per-run reports stay out; `eval/data` is kept because the seed reads the fixture document from it and `frontend-seed` mounts it for the synthetic uploads).

## Environment variables

Every variable is a field on `app.config.Settings`; `.env.example` lists them all with live defaults and a commented synthetic block (plus `APP_URL`, `BACKEND_SERVICE_SECRET` and `DEMO_USER_SWITCH`, which only the compose files read). The repo-root `.env` is read from any working directory; exported variables override it. A blank key (`ANTHROPIC_API_KEY=`) reads as unset, and both processes refuse to start when the selected provider has none. The ones you will touch:

- `DATABASE_URL`: SQLAlchemy URL with the psycopg driver.
- `SERVICE_SECRET` (32+ characters): when set, every request must carry `Authorization: Bearer <secret>`; unset, the API is open, which is fine for a native run against `dev_db.py`. Compose always sets it.
- `SYNTHETIC_DEMO` (`false` by default; `true` only with both providers `fake`): selects the heuristic providers and gates both upload routes by checksum to the `eval/data` files.
- `LLM_PROVIDER` (`anthropic` | `fake`) and `ANTHROPIC_API_KEY`; models `MODEL_MAIN` (synthesis, extraction, query rewrite) and `MODEL_FAST` (classification, coverage judgement, entailment, chunk annotation).
- `EMBEDDING_PROVIDER` (`openai` | `voyage` | `fake`), `OPENAI_API_KEY` or `VOYAGE_API_KEY`, `EMBEDDING_DIMENSION` (changing it is a migration and a re-embed of the library).
- `STORAGE_PATH` (uploaded files; everything downstream reads `document_sections`, never the file), `DOCLING_ARTIFACTS_PATH`, `FIXTURE_DOCUMENT_PATH`.
- `MAX_UPLOAD_BYTES` (default `26214400`, 25 MiB): the largest file `POST /documents` and `POST /tenders/{id}/documents` accept. Both routes read the upload in chunks and answer 413 as soon as the limit is passed, before the synthetic checksum, any storage write or the document row, so an oversized file is never held in memory or left on disk. A pure-ASGI guard on the same two routes (`app.main.UploadBodyLimit`) also bounds the request body itself, since FastAPI parses the multipart body before any handler runs: a declared `Content-Length` over the limit plus a 64 KiB multipart allowance is 413 without reading a byte, and a chunked body is cut off once it passes the same bound. The 413 sentence is the one the Next.js proxy uses, and the compose stack passes the same value to the proxy so the two limits never drift.
- Thresholds as named in the plan's conventions: `DEDUP_THRESHOLD`, `VERBATIM_THRESHOLD`, `COVERAGE_FLOOR`, `REALIGN_MIN`, `SPAN_MATCH_MIN`, `EXTRACTION_FIDELITY_TARGET`, `PAIR_EXTRACTION_WINDOW_TOKENS`, `MAX_ATTEMPTS`, `CHUNK_TARGET_TOKENS`, `RRF_K`, `CANDIDATES_PER_LIST`, `TOP_K_SYNTHESIS`, `COVERAGE_JUDGEMENT_TOP`.
- Concurrency and the worker: `TRIAGE_CONCURRENCY`, `DRAFT_ALL_CONCURRENCY`, `WORKER_POLL_INTERVAL_SECONDS`, `EXPIRY_SWEEP_INTERVAL_SECONDS`. The API and worker size their connection pool from the larger of the two concurrencies plus two, with an overflow of five.
- `REFUSAL_FALLBACKS` (`true` by default): on the models that accept it (Opus 5 and the other current models listed in `app/llm/client.py`), a request the model's safety classifier declines is re-run server-side on Anthropic's recommended fallback model (`fallbacks: "default"`); a refusal that survives it fails the call with a plain message and nothing is saved.
- `CORS_ORIGINS`: the browser origins allowed to call the API, comma-separated (default `http://localhost:3000`, the Next.js development server). Never `*`; list each environment's front-end origin.

## Headers

Every mutating request carries `X-Actor` (the user's display name); a missing header is a 400 and `system` is never accepted from clients. `X-Org-Id` is optional and defaults to the seeded organisation. Request bodies never carry the actor.

## Error bodies

Every 4xx body is a flat JSON object with a string `detail` (the message to show) and, where the plan gives the client something to act on, extra keys beside it, never nested under `detail`. The one exception is FastAPI's own validation error for a malformed body, query or path, where `detail` is the standard list of `{loc, msg, type}` entries. The shapes the routers return:

409, the request conflicts with the current state:

- `PATCH /questions/{id}` with a `status` the gates refuse: `{detail, to, blockers: [{kind: "segment" | "gap" | "needs_review" | "no_answer" | "system_only", index?, gap?}], question}`, the same `blockers` shape as `allowed_transitions`. The other fields in the body (`assignee`, `compliance_class`, `compliant_by`) are never gated: they are applied and committed even when the status is refused, and `question` is the row as it now stands.
- `POST /questions/{id}/answers` whose `base_version_id` is not the current version: `{detail, current_answer}` (`current_answer` is the full answer record, or null when the question has no version); nothing is written.
- `POST /questions/{id}/draft`, `POST /questions/{id}/verbatim` and `POST /threads/{id}/messages`: `{detail, code}` with `code` one of `displacement` (status past `ai_draft` and no `confirm_displace: true`; the body also carries `current_answer`, the version that would be displaced), `in_progress` (a draft or reply is already running), `pricing` (pricing questions are never drafted) and, for verbatim only, `ineligible` (an unverified item or one from a superseded document) and `empty` (the item's answer slice holds no sentences).
- `POST /messages/{id}/save-as-answer`: `{detail, code: "displacement", current_answer, status}` for the displacement case, so the interface's one confirmation dialog serves the card draft, verbatim and save-as-answer alike; `{detail}` for a message that cannot be saved (a user message, a message without segments, a tender-level thread, a pricing question).
- `POST /answers/{id}/segments/{index}/attest` on a segment that is not `unsupported`, `weak` or `human_authored`, `POST .../dispute` on one that is not `supported`, or either on a version that is not current: `{detail}`.
- `POST /tenders/{id}/documents` with a second question pack: `{detail}`, plus the existing pack's id in the `X-Document-Id` response header.
- `POST /tenders/{id}/submit` on a tender already submitted (`submitted_at` set, even if it has since been archived): `{detail}`; on an archived tender that was never submitted: `{detail}` telling the caller to restore it first (`PATCH {status: "open"}`), so submit never doubles as an unarchive.
- `DELETE /tenders/{id}` on a submitted tender (archive it instead), or while anything is still writing into its rows: a queued or running job whose payload names the tender or one of its documents (including the parse-only `ingest_document` job, which carries only `document_id`), a question draft in progress, or a reply streaming on one of its threads: `{detail}` ("still being processed").
- `GET /tenders/{id}/export` in `submission` mode while any question has `needs_review`: `{detail, code: "needs_review", question_ids}`; the expiry sweep has run and been committed first, so the list is current.
- `PATCH /documents/{id}` on a tender document, or before a classification has been proposed (unless the body sets `doc_type`); `POST /documents/{id}/confirm` before a proposal; `POST /documents/{id}/pairs` naming a fragment that has already been paired: `{detail}`.

422, the request is well formed but cannot be applied:

- `POST /questions/{id}/answers` with text the authoritative sentence splitter reduces to no sentences (whitespace only): `{detail}`; nothing is written.
- `POST /questions/{id}/gaps/acknowledge` with a `gap` that matches no string in the current answer's `gaps` after normalisation: `{detail}`.
- `POST /threads/{id}/messages` with content that is blank after trimming: `{detail}`.
- `POST /documents` or `POST /tenders/{id}/documents` with an empty file (refused by the shared bounded read before the synthetic checksum, any storage write or the document row, so a question pack never occupies the tender's one pack slot); `PATCH /documents/{id}` with no fields, or with a change the corrections step refuses (an unknown kind, a date that cannot be parsed); `POST /documents/{id}/pairs` with a `section_id` from another document, a span outside the section, or a manual pairing with neither `question_fragment_id` nor `question_text`; `POST /library/supersession-decisions/{id}` with `decision: "superseded"` and no `superseding_document_id`, or a decision the rule refuses; `POST /tenders/{id}/documents` with an unknown `tender_doc_kind` (a form field, so it is checked by the router, not by the body validator): `{detail}`. An unknown `format` or `mode` on `GET /tenders/{id}/export` is a query-parameter validation error, so it comes back in the list form.

413, the upload is larger than `MAX_UPLOAD_BYTES`:

- `POST /documents` and `POST /tenders/{id}/documents`: `{detail}` is `This file is too large. Uploads are limited to {N} MB.`, where `N` is the limit in MiB as a whole number when it is one and otherwise to one decimal, never rounded up (`25`, `1.5`); the Next.js proxy uses the same sentence and formatter. The file is read in chunks and refused as soon as the limit is passed, so nothing is written to storage and no document or job row exists; a request whose `Content-Length` exceeds the limit plus a 64 KiB multipart allowance is refused before the body is read, and a chunked body is cut off at the same bound, which a still-sending client may see as a reset rather than the JSON body.

Streaming endpoints return their 409 before the stream starts; once the 200 has been sent, a failure arrives as the terminal `error` event `{type: "error", code, message}` and nothing is persisted.

## Walkthrough with curl

The six MVP steps against a running API and worker, using the synthetic data in `../eval/data/`. It targets the native run from the section above (`http://localhost:8000`), because the compose stack publishes no API port. Every request needs `Authorization: Bearer $SERVICE_SECRET` when the API has a secret, which the compose stack always does; for a native run against `dev_db.py` the secret may be left unset, and the header is then ignored, so the same commands work either way. Set these once:

```sh
API=http://localhost:8000
SERVICE_SECRET=${SERVICE_SECRET:-}   # the API's SERVICE_SECRET, or empty for an open native run
ACTOR='X-Actor: Puru'                # every write needs it
JSON='Content-Type: application/json'
api() { curl -s -H "Authorization: Bearer $SERVICE_SECRET" "$@"; }
poll() { until api "$API/jobs/$1" | grep -Eq '"status": *"(done|failed)"'; do sleep 2; done; api "$API/jobs/$1"; }
```

`api` is `curl -s` with the service header; every command below uses it. `jq` is used for readability; every response is plain JSON if you prefer to read it raw. With `SYNTHETIC_DEMO=true` only the `../eval/data/` files upload; any other bytes are refused with 422 on both upload routes.

### 1. Library: upload, confirm, watch the ingest, see supersession

```sh
# Upload the three past submissions and the two reference documents (the five library files the
# e2e walk in tests/e2e uploads). Each returns the document and an ingest_document job_id; the
# worker parses, classifies, extracts, embeds and links. The Northern Fells file is the
# evaluation harness's held-out submission: the harness ingests its own library without it and
# scores drafts against its answers, so it belongs in this demo library but never in a library
# the harness will score.
for f in ../eval/data/past_submission_westmoor_icb_2024.docx \
         ../eval/data/past_submission_harbourside_fT_2025.docx \
         ../eval/data/past_submission_northern_fells_2025_heldout.docx \
         ../eval/data/reference_iso27001_certificate_2024.docx \
         ../eval/data/reference_iso27001_certificate_2025.docx; do
  api -H "$ACTOR" -F "file=@$f" "$API/documents" | jq '{id, filename, job_id}'
done

# Poll the job (or GET /documents/{id} until doc_type is set), then confirm the proposal.
poll <job_id> | jq '{status, done, total, error}'
api "$API/documents/<document_id>" | jq '{doc_type, doc_kind, effective_date, effective_date_source, classification_confirmed, ingest_status, item_counts, fact_count}'
api -X POST -H "$ACTOR" "$API/documents/<document_id>/confirm" | jq '{classification_confirmed}'
# Override instead of confirming: PATCH any of doc_type, doc_kind, effective_date, buyer, submission_date.
api -X PATCH -H "$ACTOR" -H "$JSON" -d '{"doc_kind": "iso_27001", "effective_date": "2025-03-09"}' "$API/documents/<document_id>"

# The library list, one document's sections, its unpaired queue, and pending supersession decisions.
api "$API/documents" | jq '.[] | {filename, doc_type, ingest_status, superseded_by, item_counts}'
api "$API/documents/<document_id>/sections" | jq 'length'
api "$API/documents/<document_id>/unpaired" | jq '{items: (.items|length), fragments: (.fragments|length)}'
api "$API/library/supersession-decisions" | jq .
# Once both ISO 27001 certificates are confirmed the 2024 one shows superseded_by the 2025 one.
```

### 2. Tender: create, upload the question pack, watch extraction then triage

```sh
TENDER=$(api -X POST -H "$ACTOR" -H "$JSON" -d '{"name": "Northern Fells 2025", "buyer": "Northern Fells NHS Foundation Trust", "deadline": "2026-10-31T17:00:00Z", "regime": "procurement_act"}' "$API/tenders" | jq -r .id)

# The question pack returns the extract_questions job; that job chains triage_tender as next_job_id.
UPLOAD=$(api -X POST -H "$ACTOR" -F tender_doc_kind=question_pack -F "file=@../eval/data/question_pack_northern_fells_2025.xlsx" "$API/tenders/$TENDER/documents")
EXTRACT=$(echo "$UPLOAD" | jq -r .job_id)
poll $EXTRACT | jq '{status, done, total, next_job_id}'
TRIAGE=$(api "$API/jobs/$EXTRACT" | jq -r .next_job_id)
poll $TRIAGE | jq '{status, done, total}'

# The board: every card in one call, with coverage, status, compliance class and current_answer summary.
api "$API/tenders/$TENDER/questions" | jq '.[] | {id, section, number, coverage, status, word_limit, thread_id}'
api "$API/tenders/$TENDER" | jq '{questions_total, questions_approved, needs_review_count, c_count, unclassified_mandatory_count}'
```

### 3. Draft one question and follow the stream

```sh
Q=<question_id>
# NDJSON: verbatim (when offered), segment (support_status pending), gaps, fact_checklist,
# support (one per segment, with verified offsets), done (the persisted answer). -N disables
# buffering so the lines arrive as they are produced. Past ai_draft the call is 409
# {detail, code: "displacement", current_answer} until the body carries confirm_displace: true.
api -N -X POST -H "$ACTOR" -H "$JSON" -d '{}' "$API/questions/$Q/draft"

# Open a cited source at its paragraph: locator.section_id, start and end come from any segment's source.
api "$API/sections/<section_id>?start=<start>&end=<end>" | jq '{highlight, document: .document.filename}'
```

### 4. Review: assistant thread, edit, attest, dispute, acknowledge, classify, assign, approve

```sh
THREAD=$(api "$API/questions/$Q" | jq -r .thread_id)
api -N -X POST -H "$ACTOR" -H "$JSON" -d '{"content": "Make this shorter."}' "$API/threads/$THREAD/messages"
MSG=<assistant message id from the done event>
api -X POST -H "$ACTOR" -H "$JSON" -d '{}' "$API/messages/$MSG/save-as-answer" | jq '.question.status'   # ai_draft

# Edit: the server re-splits and re-aligns; the question lands at writer_edited. A stale
# base_version_id is 409 {detail, current_answer}; whitespace-only text is 422 {detail}.
ANSWER=$(api "$API/questions/$Q" | jq -r .current_answer.id)
TEXT=$(api "$API/questions/$Q" | jq -r .current_answer.text)
api -X POST -H "$ACTOR" -H "$JSON" -d "$(jq -n --arg t "$TEXT We also run a named clinical safety officer." --arg b "$ANSWER" '{text: $t, base_version_id: $b}')" "$API/questions/$Q/answers" | jq '{status: .question.status, summary: .answer.support_summary}'
ANSWER=$(api "$API/questions/$Q" | jq -r .current_answer.id)

# Version history with segments, attest a human-authored or unsupported sentence, dispute a supported one.
api "$API/questions/$Q/answers" | jq '.[] | {version, author_type, support_summary}'
api -X POST -H "$ACTOR" -H "$JSON" -d '{"note": "Confirmed against the safety case."}' "$API/answers/$ANSWER/segments/<index>/attest" | jq '.answer.segments[<index>].support_status'
api -X POST -H "$ACTOR" -H "$JSON" -d '{"note": "The certificate number is out of date."}' "$API/answers/$ANSWER/segments/<index>/dispute" | jq '.question.needs_review'

# Gaps, compliance class, assignment, evidence, a comment, the event log.
api -X POST -H "$ACTOR" -H "$JSON" -d '{"gap": "<a string from current_answer.gaps>", "note": "Covered in the attachment."}' "$API/questions/$Q/gaps/acknowledge"
api -X PATCH -H "$ACTOR" -H "$JSON" -d '{"compliance_class": "A", "assignee": "Asha"}' "$API/questions/$Q" | jq '{compliance_class, assignee}'
api -X POST -H "$ACTOR" -H "$JSON" -d '{"document_id": "<library document id>", "note": "ISO 27001 certificate"}' "$API/questions/$Q/evidence"
api -X POST -H "$ACTOR" -H "$JSON" -d '{"text": "Ready for SME review."}' "$API/questions/$Q/comments"
api "$API/questions/$Q/events" | jq '.[] | {event_type, actor}'

# Status moves only through the transition gates; a refused move returns 409 {detail, to, blockers}.
api "$API/questions/$Q" | jq '.allowed_transitions'
api -X PATCH -H "$ACTOR" -H "$JSON" -d '{"status": "sme_verified"}' "$API/questions/$Q" | jq '.status // .detail'
api -X PATCH -H "$ACTOR" -H "$JSON" -d '{"status": "approved"}' "$API/questions/$Q" | jq '.status // .detail'
```

### 5. Table view filter, document read-through, export

```sh
# Not yet approved (the table view's filter is client-side over the same call).
api "$API/tenders/$TENDER/questions" | jq '[.[] | select(.status != "approved")] | length'
# Submission export: approved answers in the buyer's order, placeholders elsewhere. Runs the
# expiry sweep first and refuses with 409 {detail, code: "needs_review", question_ids} while any
# question needs review (see Error bodies above).
api -D - -o northern-fells-submission.docx -H "$ACTOR" "$API/tenders/$TENDER/export?format=docx&mode=submission"
# Internal review copy with each sentence's sources as Word comments, and the xlsx form:
api -o northern-fells-review.docx "$API/tenders/$TENDER/export?format=docx&mode=review"
api -o northern-fells.xlsx "$API/tenders/$TENDER/export?format=xlsx&mode=submission"

# Accelerators: re-triage after new library uploads; draft every not_started covered/partial question.
api -X POST -H "$ACTOR" "$API/tenders/$TENDER/retriage" | jq '{id, kind}'
api -X POST -H "$ACTOR" -H "$JSON" -d '{"include_new": false}' "$API/tenders/$TENDER/draft-all" | jq '{id, total}'

# Submission promotes approved answers into the library; a later outcome re-tags them.
api -X POST -H "$ACTOR" "$API/tenders/$TENDER/submit" | jq '{status: .tender.status, promoted}'
api -X PATCH -H "$ACTOR" -H "$JSON" -d '{"outcome": "won", "outcome_notes": "Scored 87%"}' "$API/tenders/$TENDER" | jq '{outcome}'
```

### 6. The evaluation report

The harness writes `../eval/reports/<timestamp>.md` (and a `.json` twin) per run over the same synthetic data; open the latest `.md`. Regenerate the synthetic data with `python -m eval.generate_synthetic --out eval/data` from the repo root (`--mode template` needs no key).

### Fixtures for the front end

`GET /fixtures/answer` returns a question, answer and thread in the live shapes with every support status present, and `POST /fixtures/draft` (`?fail_after=3`, `?delay_ms=0`) replays the streaming contract without persisting anything.

## Layout

See the repository layout in `../CLAUDE.md`. Module owners register job handlers with `app.jobs.register(kind)`; `app/worker.py` imports the handler modules by name at start-up. Alembic migrations live in `alembic/versions/`; enumerations are strings guarded by CHECK constraints, so adding a value is a migration that recreates the constraint (see `0002`).

## Authenticated frontend integration

The combined stack keeps the API private. `SERVICE_SECRET` (32+ characters) enables a
constant-time bearer-secret check on every endpoint, including health and OpenAPI. This is
service authentication only; Next.js owns user sessions and business membership.

`GET /health` now also reports `llm_provider`, `embedding_provider`, `synthetic_demo` and
`service_secret_enabled`. The frontend refuses demo startup unless both providers are live
and service protection is enabled.

`POST /organisations` accepts `{id, name}` and initialises the default topic taxonomy.
The caller supplies a stable UUID, so retrying the same request is idempotent; reusing an ID
with a different name returns 409. This is an operator/server provisioning path and is not
included in the browser proxy allow-list.

`GET /documents/{id}/file` returns the original file after the same organisation check as
the document resource. Missing files and paths outside the configured storage root return
404. Downloads carry the original filename.

`SYNTHETIC_DEMO=true` explicitly selects the heuristic provider previously used only by the
end-to-end tests. Both providers must be `fake`, and the API and worker refuse to start
otherwise. Both upload routes, `POST /documents` and `POST /tenders/{id}/documents`, go through
one `store_upload` and are restricted by checksum to the files in `eval/data`; arbitrary and
real documents are rejected with 422 (`tests/test_frontend_integration.py` covers both routes).
This mode proves plumbing, not answer quality. The joined compose stack defaults to it so it
runs without keys; `.env.example` carries the live defaults, and `docker-compose.live.yml`
turns it off explicitly. Leave it off for normal tests and live deployments.

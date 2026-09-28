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

```sh
docker compose up --build
```

`db` is `pgvector/pgvector:pg16`; `migrate` builds the image (tagged `tenders-backend`) and applies the migrations and the seed once; `api` (`uvicorn app.main:app --workers 1` on port 8000) and `worker` (`python -m app.worker`) start from that same image after it completes and share the `storage` volume for uploaded files. Docling's PDF layout and TableFormer weights are downloaded into `DOCLING_ARTIFACTS_PATH` (`/opt/docling-models`) at image build time; nothing is fetched at start-up. A `.env` at the repo root is read by every service for provider keys; the connection, storage and model-weight paths are pinned in `docker-compose.yml` so a local `.env` cannot redirect the containers.

Notes on the image and the ports:

- The image is CPU-only: `uv pip install --torch-backend cpu` takes torch and torchvision from the CPU wheel index, so the CUDA libraries PyPI's Linux build would pull in are never downloaded. It installs `libgl1` and `libglib2.0-0t64`, which opencv (via docling's OCR extra) needs even with OCR off; without them every PDF ingest fails at run time.
- The containers run as the unprivileged user `app` (uid 10001). `/data/storage` is created and owned in the image, so the `storage` volume inherits that ownership when compose first creates it. A volume created by an earlier root-running image needs `docker compose down -v` (or a one-off `docker compose run --rm --user root api chown 10001 /data/storage`), otherwise uploads fail to create their directories.
- The database is published on the loopback interface only, at `127.0.0.1:5432`, which is what `.env.example`'s `DATABASE_URL` points at for the harness or a locally run API. If a local Postgres already holds 5432, set `TENDERS_DB_PORT=54320` (any free port) in the repo-root `.env`; compose interpolates it, `Settings` ignores the extra key, and you then use that port in `DATABASE_URL` when reaching the compose database from the host. The API is published on every interface at 8000, as the plan's unauthenticated API intends.
- The build context is the repo root trimmed by `/.dockerignore` (`.venv`, `.git`, `.devdb`, `storage`, `.env`, caches and per-run reports stay out; `eval/data` is kept because the seed reads the fixture document from it).

## Environment variables

Every variable is a field on `app.config.Settings`; `.env.example` lists them all with defaults (plus `TENDERS_DB_PORT`, which only `docker-compose.yml` reads). The repo-root `.env` is read from any working directory; exported variables override it. A blank key (`ANTHROPIC_API_KEY=`) reads as unset, and both processes refuse to start when the selected provider has none. The ones you will touch:

- `DATABASE_URL`: SQLAlchemy URL with the psycopg driver.
- `LLM_PROVIDER` (`anthropic` | `fake`) and `ANTHROPIC_API_KEY`; models `MODEL_MAIN` (synthesis, extraction, query rewrite) and `MODEL_FAST` (classification, coverage judgement, entailment, chunk annotation).
- `EMBEDDING_PROVIDER` (`openai` | `voyage` | `fake`), `OPENAI_API_KEY` or `VOYAGE_API_KEY`, `EMBEDDING_DIMENSION` (changing it is a migration and a re-embed of the library).
- `STORAGE_PATH` (uploaded files; everything downstream reads `document_sections`, never the file), `DOCLING_ARTIFACTS_PATH`, `FIXTURE_DOCUMENT_PATH`.
- Thresholds as named in the plan's conventions: `DEDUP_THRESHOLD`, `VERBATIM_THRESHOLD`, `COVERAGE_FLOOR`, `REALIGN_MIN`, `SPAN_MATCH_MIN`, `EXTRACTION_FIDELITY_TARGET`, `PAIR_EXTRACTION_WINDOW_TOKENS`, `MAX_ATTEMPTS`, `CHUNK_TARGET_TOKENS`, `RRF_K`, `CANDIDATES_PER_LIST`, `TOP_K_SYNTHESIS`, `COVERAGE_JUDGEMENT_TOP`.
- Concurrency and the worker: `TRIAGE_CONCURRENCY`, `DRAFT_ALL_CONCURRENCY`, `WORKER_POLL_INTERVAL_SECONDS`, `EXPIRY_SWEEP_INTERVAL_SECONDS`. The API and worker size their connection pool from the larger of the two concurrencies plus two, with an overflow of five.
- `CORS_ORIGINS`: the browser origins allowed to call the API, comma-separated (default `http://localhost:3000`, the Next.js development server). Never `*`; list each environment's front-end origin.

## Headers

Every mutating request carries `X-Actor` (the user's display name); a missing header is a 400 and `system` is never accepted from clients. `X-Org-Id` is optional and defaults to the seeded organisation. Request bodies never carry the actor.

## Error bodies

Every 4xx body is a flat JSON object with a string `detail` (the message to show) and, where the plan gives the client something to act on, extra keys beside it, never nested under `detail`. The one exception is FastAPI's own validation error for a malformed body, query or path, where `detail` is the standard list of `{loc, msg, type}` entries. The shapes the routers return:

409, the request conflicts with the current state:

- `PATCH /questions/{id}` with a `status` the gates refuse: `{detail, to, blockers: [{kind: "segment" | "gap" | "needs_review" | "no_answer" | "system_only", index?, gap?}]}`, the same `blockers` shape as `allowed_transitions`.
- `POST /questions/{id}/answers` whose `base_version_id` is not the current version: `{detail, current_answer}` (`current_answer` is the full answer record, or null when the question has no version); nothing is written.
- `POST /questions/{id}/draft`, `POST /questions/{id}/verbatim` and `POST /threads/{id}/messages`: `{detail, code}` with `code` one of `displacement` (status past `ai_draft` and no `confirm_displace: true`; the body also carries `current_answer`, the version that would be displaced), `in_progress` (a draft or reply is already running), `pricing` (pricing questions are never drafted) and, for verbatim only, `ineligible` (an unverified item or one from a superseded document) and `empty` (the item's answer slice holds no sentences).
- `POST /messages/{id}/save-as-answer`: `{detail, code: "displacement", current_answer, status}` for the displacement case, so the interface's one confirmation dialog serves the card draft, verbatim and save-as-answer alike; `{detail}` for a message that cannot be saved (a user message, a message without segments, a tender-level thread, a pricing question).
- `POST /answers/{id}/segments/{index}/attest` on a segment that is not `unsupported`, `weak` or `human_authored`, `POST .../dispute` on one that is not `supported`, or either on a version that is not current: `{detail}`.
- `POST /tenders/{id}/documents` with a second question pack: `{detail}`, plus the existing pack's id in the `X-Document-Id` response header.
- `POST /tenders/{id}/submit` on a tender already submitted: `{detail}`.
- `GET /tenders/{id}/export` in `submission` mode while any question has `needs_review`: `{detail, code: "needs_review", question_ids}`; the expiry sweep has run and been committed first, so the list is current.
- `PATCH /documents/{id}` on a tender document, or before a classification has been proposed (unless the body sets `doc_type`); `POST /documents/{id}/confirm` before a proposal; `POST /documents/{id}/pairs` naming a fragment that has already been paired: `{detail}`.

422, the request is well formed but cannot be applied:

- `POST /questions/{id}/answers` with text the authoritative sentence splitter reduces to no sentences (whitespace only): `{detail}`; nothing is written.
- `POST /questions/{id}/gaps/acknowledge` with a `gap` that matches no string in the current answer's `gaps` after normalisation: `{detail}`.
- `POST /threads/{id}/messages` with content that is blank after trimming: `{detail}`.
- `POST /documents` with an empty file; `PATCH /documents/{id}` with no fields, or with a change the corrections step refuses (an unknown kind, a date that cannot be parsed); `POST /documents/{id}/pairs` with a `section_id` from another document, a span outside the section, or a manual pairing with neither `question_fragment_id` nor `question_text`; `POST /library/supersession-decisions/{id}` with `decision: "superseded"` and no `superseding_document_id`, or a decision the rule refuses; `POST /tenders/{id}/documents` with an unknown `tender_doc_kind` (a form field, so it is checked by the router, not by the body validator): `{detail}`. An unknown `format` or `mode` on `GET /tenders/{id}/export` is a query-parameter validation error, so it comes back in the list form.

Streaming endpoints return their 409 before the stream starts; once the 200 has been sent, a failure arrives as the terminal `error` event `{type: "error", code, message}` and nothing is persisted.

## Walkthrough with curl

The six MVP steps against a running API and worker, using the synthetic data in `../eval/data/`. Set these once:

```sh
API=http://localhost:8000
ACTOR='X-Actor: Puru'          # every write needs it
JSON='Content-Type: application/json'
poll() { until curl -s "$API/jobs/$1" | grep -Eq '"status": *"(done|failed)"'; do sleep 2; done; curl -s "$API/jobs/$1"; }
```

`jq` is used below for readability; every response is plain JSON if you prefer to read it raw.

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
  curl -s -H "$ACTOR" -F "file=@$f" "$API/documents" | jq '{id, filename, job_id}'
done

# Poll the job (or GET /documents/{id} until doc_type is set), then confirm the proposal.
poll <job_id> | jq '{status, done, total, error}'
curl -s "$API/documents/<document_id>" | jq '{doc_type, doc_kind, effective_date, effective_date_source, classification_confirmed, ingest_status, item_counts, fact_count}'
curl -s -X POST -H "$ACTOR" "$API/documents/<document_id>/confirm" | jq '{classification_confirmed}'
# Override instead of confirming: PATCH any of doc_type, doc_kind, effective_date, buyer, submission_date.
curl -s -X PATCH -H "$ACTOR" -H "$JSON" -d '{"doc_kind": "iso_27001", "effective_date": "2025-03-09"}' "$API/documents/<document_id>"

# The library list, one document's sections, its unpaired queue, and pending supersession decisions.
curl -s "$API/documents" | jq '.[] | {filename, doc_type, ingest_status, superseded_by, item_counts}'
curl -s "$API/documents/<document_id>/sections" | jq 'length'
curl -s "$API/documents/<document_id>/unpaired" | jq '{items: (.items|length), fragments: (.fragments|length)}'
curl -s "$API/library/supersession-decisions" | jq .
# Once both ISO 27001 certificates are confirmed the 2024 one shows superseded_by the 2025 one.
```

### 2. Tender: create, upload the question pack, watch extraction then triage

```sh
TENDER=$(curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{"name": "Northern Fells 2025", "buyer": "Northern Fells NHS Foundation Trust", "deadline": "2026-10-31T17:00:00Z", "regime": "procurement_act"}' "$API/tenders" | jq -r .id)

# The question pack returns the extract_questions job; that job chains triage_tender as next_job_id.
UPLOAD=$(curl -s -X POST -H "$ACTOR" -F tender_doc_kind=question_pack -F "file=@../eval/data/question_pack_northern_fells_2025.xlsx" "$API/tenders/$TENDER/documents")
EXTRACT=$(echo "$UPLOAD" | jq -r .job_id)
poll $EXTRACT | jq '{status, done, total, next_job_id}'
TRIAGE=$(curl -s "$API/jobs/$EXTRACT" | jq -r .next_job_id)
poll $TRIAGE | jq '{status, done, total}'

# The board: every card in one call, with coverage, status, compliance class and current_answer summary.
curl -s "$API/tenders/$TENDER/questions" | jq '.[] | {id, section, number, coverage, status, word_limit, thread_id}'
curl -s "$API/tenders/$TENDER" | jq '{questions_total, questions_approved, needs_review_count, c_count, unclassified_mandatory_count}'
```

### 3. Draft one question and follow the stream

```sh
Q=<question_id>
# NDJSON: verbatim (when offered), segment (support_status pending), gaps, fact_checklist,
# support (one per segment, with verified offsets), done (the persisted answer). -N disables
# buffering so the lines arrive as they are produced. Past ai_draft the call is 409
# {detail, code: "displacement", current_answer} until the body carries confirm_displace: true.
curl -s -N -X POST -H "$ACTOR" -H "$JSON" -d '{}' "$API/questions/$Q/draft"

# Open a cited source at its paragraph: locator.section_id, start and end come from any segment's source.
curl -s "$API/sections/<section_id>?start=<start>&end=<end>" | jq '{highlight, document: .document.filename}'
```

### 4. Review: assistant thread, edit, attest, dispute, acknowledge, classify, assign, approve

```sh
THREAD=$(curl -s "$API/questions/$Q" | jq -r .thread_id)
curl -s -N -X POST -H "$ACTOR" -H "$JSON" -d '{"content": "Make this shorter."}' "$API/threads/$THREAD/messages"
MSG=<assistant message id from the done event>
curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{}' "$API/messages/$MSG/save-as-answer" | jq '.question.status'   # ai_draft

# Edit: the server re-splits and re-aligns; the question lands at writer_edited. A stale
# base_version_id is 409 {detail, current_answer}; whitespace-only text is 422 {detail}.
ANSWER=$(curl -s "$API/questions/$Q" | jq -r .current_answer.id)
TEXT=$(curl -s "$API/questions/$Q" | jq -r .current_answer.text)
curl -s -X POST -H "$ACTOR" -H "$JSON" -d "$(jq -n --arg t "$TEXT We also run a named clinical safety officer." --arg b "$ANSWER" '{text: $t, base_version_id: $b}')" "$API/questions/$Q/answers" | jq '{status: .question.status, summary: .answer.support_summary}'
ANSWER=$(curl -s "$API/questions/$Q" | jq -r .current_answer.id)

# Version history with segments, attest a human-authored or unsupported sentence, dispute a supported one.
curl -s "$API/questions/$Q/answers" | jq '.[] | {version, author_type, support_summary}'
curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{"note": "Confirmed against the safety case."}' "$API/answers/$ANSWER/segments/<index>/attest" | jq '.answer.segments[<index>].support_status'
curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{"note": "The certificate number is out of date."}' "$API/answers/$ANSWER/segments/<index>/dispute" | jq '.question.needs_review'

# Gaps, compliance class, assignment, evidence, a comment, the event log.
curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{"gap": "<a string from current_answer.gaps>", "note": "Covered in the attachment."}' "$API/questions/$Q/gaps/acknowledge"
curl -s -X PATCH -H "$ACTOR" -H "$JSON" -d '{"compliance_class": "A", "assignee": "Asha"}' "$API/questions/$Q" | jq '{compliance_class, assignee}'
curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{"document_id": "<library document id>", "note": "ISO 27001 certificate"}' "$API/questions/$Q/evidence"
curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{"text": "Ready for SME review."}' "$API/questions/$Q/comments"
curl -s "$API/questions/$Q/events" | jq '.[] | {event_type, actor}'

# Status moves only through the transition gates; a refused move returns 409 {detail, to, blockers}.
curl -s "$API/questions/$Q" | jq '.allowed_transitions'
curl -s -X PATCH -H "$ACTOR" -H "$JSON" -d '{"status": "sme_verified"}' "$API/questions/$Q" | jq '.status // .detail'
curl -s -X PATCH -H "$ACTOR" -H "$JSON" -d '{"status": "approved"}' "$API/questions/$Q" | jq '.status // .detail'
```

### 5. Table view filter, document read-through, export

```sh
# Not yet approved (the table view's filter is client-side over the same call).
curl -s "$API/tenders/$TENDER/questions" | jq '[.[] | select(.status != "approved")] | length'
# Submission export: approved answers in the buyer's order, placeholders elsewhere. Runs the
# expiry sweep first and refuses with 409 {detail, code: "needs_review", question_ids} while any
# question needs review (see Error bodies above).
curl -s -D - -o northern-fells-submission.docx -H "$ACTOR" "$API/tenders/$TENDER/export?format=docx&mode=submission"
# Internal review copy with each sentence's sources as Word comments, and the xlsx form:
curl -s -o northern-fells-review.docx "$API/tenders/$TENDER/export?format=docx&mode=review"
curl -s -o northern-fells.xlsx "$API/tenders/$TENDER/export?format=xlsx&mode=submission"

# Accelerators: re-triage after new library uploads; draft every not_started covered/partial question.
curl -s -X POST -H "$ACTOR" "$API/tenders/$TENDER/retriage" | jq '{id, kind}'
curl -s -X POST -H "$ACTOR" -H "$JSON" -d '{"include_new": false}' "$API/tenders/$TENDER/draft-all" | jq '{id, total}'

# Submission promotes approved answers into the library; a later outcome re-tags them.
curl -s -X POST -H "$ACTOR" "$API/tenders/$TENDER/submit" | jq '{status: .tender.status, promoted}'
curl -s -X PATCH -H "$ACTOR" -H "$JSON" -d '{"outcome": "won", "outcome_notes": "Scored 87%"}' "$API/tenders/$TENDER" | jq '{outcome}'
```

### 6. The evaluation report

The harness writes `../eval/reports/<timestamp>.md` (and a `.json` twin) per run over the same synthetic data; open the latest `.md`. Regenerate the synthetic data with `python -m eval.generate_synthetic --out eval/data` from the repo root (`--mode template` needs no key).

### Fixtures for the front end

`GET /fixtures/answer` returns a question, answer and thread in the live shapes with every support status present, and `POST /fixtures/draft` (`?fail_after=3`, `?delay_ms=0`) replays the streaming contract without persisting anything.

## Layout

See the repository layout in `../CLAUDE.md`. Module owners register job handlers with `app.jobs.register(kind)`; `app/worker.py` imports the handler modules by name at start-up. Alembic migrations live in `alembic/versions/`; enumerations are strings guarded by CHECK constraints, so adding a value is a migration that recreates the constraint (see `0002`).

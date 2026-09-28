# CLAUDE.md

Project context and build plan for a tender-response application. Read this file fully before writing any code. It records the decisions made during planning; where a decision is marked as an assumption, check with Puru before building on it.

## What this project is

A web application that helps technology companies respond to procurement tenders (NHS tenders are the first use case, but the product is general). The company uploads its past tender submissions and its current compliance and reference documents. When a new tender arrives, the company uploads the buyer's question pack, and the application extracts every question, triages which questions are already answerable from existing material, drafts answers with provenance, and tracks every question through human review to submission.

This is a hackathon MVP that is intended to become a SaaS product. Puru owns the backend and the pipeline. A colleague owns the visual design and front-end implementation. This file specifies the backend, the pipelines, and the information architecture the front end must present; it does not specify visual design.

## The core thesis

The chat is not the product. A general-purpose assistant can already take four PDFs and draft an answer to a pasted question. What it cannot do is know that this tender has forty-three questions, that eleven are already fully answerable from approved past material, that question 4.2 carries fifteen per cent of the score and nobody owns it, that the DCB0129 answer cites a superseded clinical safety case, and that the deadline is Thursday. The product is the tender as a tracked object. Chat is one tool inside it.

Every design decision should be tested against that thesis. A feature that a general assistant already offers is not a differentiator. The differentiators are: structured extraction of the question pack into tracked cards; coverage triage across the whole tender at upload time; per-question drafting with sentence-level traceability to source material, a gap list and a fact checklist; fact-level freshness against current reference documents; a review pipeline with an audit trail; export in the buyer's structure; and a learning loop that promotes approved answers back into the knowledge base.

Traceability is a first-class requirement, not a display detail. Every sentence of every generated response must be traceable to the source material it was drawn from, and checking that trace must take one hover or one click, never a search. The purpose is to let reviewers verify only what needs verifying: a reviewer who has to re-read every source to trust a draft gains nothing over writing it themselves. See the section on traceability below; it constrains the synthesis output schema, the answer data model, the editor, and the export.

## Scope for the hackathon

In scope:

- Upload past submissions and reference documents into an organisation-level library.
- Ingestion pipeline: classification, question-answer pair extraction, reference-document chunking, deduplication, fact extraction, supersession, embedding.
- Upload a buyer's question pack for a new tender; extract questions into cards with constraints.
- Coverage triage for every question at upload time.
- Per-question drafting with citations, gap list, fact checklist, and a verbatim option when a near-identical past question exists.
- Draft-all across a tender.
- Per-question chat threads with history and a tender-level thread.
- Review pipeline with named states, assignment, comments, versioned answers, and an event log.
- Board, table and document views over the question cards.
- Export of approved answers in the buyer's section order as docx or xlsx.
- Evaluation harness built in the first phase.

Out of scope for the hackathon (design for them, do not build them):

- Authentication, user management beyond a display name, and billing.
- Multi-tenant isolation beyond an `org_id` column on every table.
- Writing answers back into the buyer's original file in place.
- Notifications, email, or integrations with Notion, Atamis or procurement portals.
- A second local model for query expansion.

## Vocabulary

Use these terms consistently in code, API and UI. The abbreviation "RAG" is ambiguous in this domain (retrieval-augmented generation versus red-amber-green readiness). In this codebase, "RAG" is never used as an identifier. Retrieval is called retrieval. Readiness colours are called coverage (for retrieval-derived readiness) and status (for review state).

- Organisation: the company using the product. Owns the library, tenders and threads.
- Library: the organisation's knowledge base, consisting of documents, knowledge items and facts.
- Document: an uploaded file. Has a `doc_type` of `past_submission` or `reference`, a `doc_kind` for reference documents, an `effective_date`, and a `superseded_by` pointer.
- Knowledge item: a retrievable unit. Either a question-answer pair extracted from a past submission, a chunk of a reference document, or an approved answer promoted from a completed tender.
- Canonical item: a knowledge item chosen to represent a cluster of near-duplicate items. Variants remain attached.
- Fact: a discrete, dated, expiring claim extracted from a document (certificate numbers, DSPT year and status, Cyber Essentials Plus expiry, ISO 27001 scope and date, named clinical safety officer, headcount, insurance limits). Has a source document, an effective date and an optional expiry.
- Tender: a procurement the organisation is responding to. Has a buyer, a deadline, a status and source documents (the question pack).
- Question: one question extracted from a question pack. Has section, number, text, word limit, weighting, `response_type`, and `mandatory` flag.
- Answer: a versioned response to a question. Each version has an author (`ai` or a user), a status, citations, and text.
- Thread: a chat conversation, scoped to a question or to a tender. Messages store retrieved item identifiers and citations.
- Coverage: retrieval-derived readiness for a question: `covered`, `partial`, or `new`.
- Status: review state of a question's current answer: `not_started`, `ai_draft`, `writer_edited`, `sme_verified`, `approved`.

## Architecture and stack

Backend is Python 3.12 with FastAPI. Persistence is Postgres 16 with the pgvector extension; no separate vector database. Full-text search uses Postgres `tsvector` for the lexical half of hybrid retrieval; no separate search service. Background work uses FastAPI `BackgroundTasks` for the hackathon, with the job interface written so it can move to a queue (arq or Celery) later without changing callers.

Document parsing uses Docling for docx, xlsx and PDF, preserving tables and section structure. LLM calls use the Anthropic SDK with Claude for classification, extraction, topic labelling, query rewriting, reranking fallback and synthesis. Embeddings use an external embedding API behind a single `embed(texts) -> vectors` function selected by environment variable (default `text-embedding-3-small`; Voyage is the alternative). Reranking uses a local cross-encoder (`BAAI/bge-reranker-v2-m3` via sentence-transformers); if that is too slow on the hackathon machine, fall back to an LLM rerank over ten candidates.

The front end is a separate Next.js application owned by the colleague and consumes the REST API. Draft generation streams over server-sent events. The backend must be runnable with a single `docker compose up` that starts Postgres with pgvector and the API.

Repository layout:

```
backend/
  app/
    main.py              FastAPI app and router registration
    config.py            settings from environment
    db/                  SQLAlchemy models, Alembic migrations, session
    ingest/              parsing, classification, extraction, chunking, dedup, facts, embedding
    retrieve/            hybrid search, fusion, rerank, coverage
    generate/            query rewrite, topic classify, synthesis, draft-all
    review/              status transitions, events, versions, promotion
    export/              docx and xlsx export
    api/                 routers, one file per resource
    llm/                 Anthropic client wrapper, prompt templates, schema-validated calls
  eval/                  evaluation harness and held-out data
  tests/
frontend/                owned by the colleague
docker-compose.yml
```

## Data model

All tables carry `id` (UUID), `org_id`, `created_at`, `updated_at`. Vector columns use pgvector with the embedding dimension set from configuration.

- `organisations`: `name`, `topic_taxonomy` (JSON list, seeded from the default taxonomy below, editable).
- `documents`: `filename`, `storage_path`, `doc_type` (`past_submission` | `reference`), `doc_kind` (nullable; e.g. `dspt_confirmation`, `cyber_essentials_plus`, `iso_27001`, `clinical_safety_case`, `information_security_policy`, `product_description`, `other`), `effective_date`, `effective_date_source` (`extracted` | `upload_time` | `user`), `superseded_by` (nullable document id), `buyer` (nullable, for past submissions), `submission_date` (nullable), `outcome` (nullable: `won` | `lost` | `unknown`), `ingest_status` (`queued` | `parsing` | `extracting` | `embedding` | `ready` | `failed`), `ingest_error`.
- `knowledge_items`: `document_id`, `item_type` (`qa_pair` | `chunk` | `promoted_answer`), `question_text` (nullable for chunks), `answer_text`, `section_locator` (JSON: page, table index, heading path), `topics` (text array), `canonical_id` (nullable; points to the canonical item of its cluster), `is_canonical`, `question_embedding` (vector, nullable), `answer_embedding` (vector), `search_tsv` (tsvector over question and answer text), `excluded_from_retrieval` (boolean; true when the source document is superseded).
- `facts`: `document_id`, `knowledge_item_id` (nullable), `fact_kind`, `statement`, `value`, `effective_date`, `expires_on` (nullable), `superseded_by` (nullable fact id).
- `tenders`: `name`, `buyer`, `deadline`, `status` (`open` | `submitted` | `archived`), `question_pack_document_id`, `submitted_at`.
- `questions`: `tender_id`, `section`, `number`, `text`, `word_limit` (nullable), `weighting` (nullable), `response_type` (`free_text` | `yes_no` | `attachment` | `table` | `other`), `mandatory` (boolean), `order_index`, `coverage` (`covered` | `partial` | `new` | `unknown`), `coverage_detail` (JSON: top candidate ids and scores, gap summary), `assignee` (nullable display name), `status`.
- `answers`: `question_id`, `version`, `author_type` (`ai` | `user`), `author_name`, `text`, `word_count`, `segments` (JSON list, see traceability section; the canonical record of what each sentence rests on), `gaps` (JSON list of strings), `fact_checklist` (JSON list of `{fact_id, statement, effective_date, status}`), `verbatim_source_item_id` (nullable), `support_summary` (JSON: counts of segments by `support_status`), `is_current`.
- `threads`: `tender_id`, `question_id` (nullable; null means tender-level thread), `title`.
- `messages`: `thread_id`, `role` (`user` | `assistant` | `system`), `content`, `segments` (JSON, same shape as on answers; null for user messages), `rewritten_query` (nullable), `retrieved_item_ids` (JSON), `support_summary` (JSON), `answer_id` (nullable; set when the message was saved as an answer).
- `events`: `entity_type`, `entity_id`, `event_type` (`status_changed` | `assigned` | `answer_created` | `answer_saved_from_chat` | `comment_added` | `document_superseded` | `answer_promoted` | ...), `actor`, `payload` (JSON).
- `comments`: `question_id`, `author`, `text`.

## Default topic taxonomy

Seed per organisation; editable. Keep to roughly ten so classification stays reliable.

`clinical_safety`, `information_governance`, `information_security`, `interoperability`, `implementation_and_onboarding`, `training_and_support`, `commercial_and_pricing`, `social_value`, `company_and_experience`, `product_functionality`, `service_levels`.

## Ingestion pipeline

Triggered on document upload. Runs asynchronously; `ingest_status` is updated at each stage and exposed to the front end so a thread can show indexing progress.

1. Parse with Docling. Preserve section headings, page numbers and tables. Produce a list of sections, each with heading path, page range, text and tables.
2. Classify the document with one LLM call over the first sections and a sample of later ones. Output schema: `doc_type`, `doc_kind` (for reference documents), `effective_date` (from content; null if absent), `buyer` and `submission_date` (for past submissions). Persist. If `effective_date` is null, set it to the upload time and record `effective_date_source = upload_time`. Surface the classification to the user as a one-line confirmation they can override; the override is recorded as `effective_date_source = user`.
3. For past submissions: extract question-answer pairs per section. The prompt describes the three common layouts (question and answer in adjacent table cells; question as heading with answer beneath; numbered form with boxed answer cell) and asks for pairs regardless of layout, each with a `section_locator`. Fragments that cannot be paired are returned as `unpaired` and exposed to the user for manual pairing. Per pair, the same call emits `topics` (from the organisation's taxonomy) and `facts` (dated or expiring claims stated in the answer).
4. For reference documents: chunk by section with a target of roughly 400 tokens and heading-path context prepended. Per chunk, emit `topics` and `facts`.
5. Facts: persist each fact with its source document and effective date. When a new fact of the same `fact_kind` arrives from a newer document, mark the older fact `superseded_by` the new one.
6. Supersession: when a reference document of the same `doc_kind` already exists, the newer `effective_date` supersedes the older. The older document is retained, its knowledge items set `excluded_from_retrieval = true`, and an event is recorded. Ties (same date) do not supersede; flag for the user.
7. Deduplication: for each new `qa_pair`, compare its answer embedding against existing canonical items in the same organisation. Above a cosine threshold of 0.90, attach it as a variant of the existing canonical item; otherwise it becomes a new canonical item. Retrieval returns canonical items with variants available on demand, so top results are diverse rather than four copies of the same paragraph.
8. Embed: `question_embedding` from question text (pairs only) and `answer_embedding` from answer or chunk text. Populate `search_tsv`.

## Question pack ingestion and coverage triage

Triggered when a question pack is uploaded to a tender.

1. Parse with Docling and extract questions with one LLM call per section. Output per question: `section`, `number`, `text`, `word_limit`, `weighting`, `response_type`, `mandatory`, `order_index`. Create one `questions` row per extracted question with `status = not_started`.
2. For every question, run retrieval (below, without synthesis) and compute coverage. `covered`: top reranked candidate above a high threshold and a cheap LLM judgement finds no material gap. `partial`: candidates exist above a low threshold but the judgement lists gaps. `new`: no candidate above the low threshold. Thresholds are configuration values to be tuned against the evaluation harness; start at 0.75 and 0.45 rerank score.
3. Persist `coverage` and `coverage_detail`. The board is now populated and colour-coded. This is the primary demo moment and must complete within about a minute for a forty-question pack; parallelise retrieval across questions.

## Draft pipeline (single question)

Inputs: question id (or free text for tender-level chat), optional conversation history, optional user instruction.

1. Query rewrite: if there is conversation history, rewrite the latest turn into a standalone retrieval question. This is contextualisation, not expansion. Skip when there is no history.
2. Topic classification: one LLM call with the organisation's taxonomy as the fixed output space; returns one to three topics.
3. Hybrid retrieval: lexical search over `search_tsv` and vector search over both `question_embedding` and `answer_embedding`, each returning twenty candidates from non-excluded items. Boost candidates whose `topics` intersect the classified topics. Fuse with reciprocal rank fusion. Take the top ten.
4. Rerank the ten with the cross-encoder against the rewritten question. Keep the top five. Retrieve related facts: facts attached to the five items plus current (non-superseded) facts whose `fact_kind` matches the topics.
5. Verbatim option: if the top candidate's question-to-question cosine similarity is 0.92 or above, return its answer text as `verbatim_source_item_id` alongside the adapted draft.
6. Synthesis: one Claude call with the question, its constraints (word limit, buyer name, response type), the five candidates with identifiers, the related facts with identifiers and dates, and any history. The prompt is a constrained composition task, not "pick the best answer". The model does not return prose; it returns a list of segments, one per sentence, each carrying the sentence text and the source identifiers and quoted source spans it rests on. Where a past answer and a current fact conflict, the fact wins and the conflict is noted. The output ends with a `gaps` list naming what the question asks for that no candidate or fact covers. Output schema: `segments`, `gaps`, `fact_checklist`. A segment with no sources is permitted only for connective sentences (a lead-in or a transition) and must be labelled `connective`; any substantive sentence without a source is a prompt failure and is rendered as unsupported.
7. Support verification: for each sourced segment, check that the quoted source span actually appears in the cited item (exact or near-exact string match after whitespace normalisation) and that the source span supports the sentence (a cheap entailment call batched across all segments, or an embedding similarity floor if latency requires). Assign `support_status` per segment. This step exists so that the trace shown to the user is a verified trace, not the model's claim about its own sources.
8. Persist as a new `answers` version with `author_type = ai` when invoked from a card, or as a message when invoked from a thread. Stream segments to the client as they are produced, with support status following once verification completes.

Draft-all runs the pipeline for every question with coverage `covered` or `partial`, in parallel with a concurrency limit, and sets each question's status to `ai_draft`.

Query expansion beyond the rewrite step is deliberately excluded. The corpus for an early customer is one to four submissions, perhaps one to three hundred items, and hybrid search plus topic boosting should reach the same recall without the latency and noise. This is a hypothesis: the evaluation harness includes a toggle for multi-query expansion so it can be measured and included later if it moves recall.

## Traceability

Every sentence in a response, whether it stands alone or sits inside a longer answer, must be traceable to the source material it came from, and the reviewer must be able to see that source without leaving the sentence. This is the mechanism by which the product earns trust; if it is weak, reviewers will re-read every source and the product saves nothing.

Segment record. The unit of traceability is the sentence. An answer's `segments` field is a list of:

```
{
  "index": 0,
  "text": "…one sentence…",
  "kind": "substantive" | "connective",
  "sources": [
    {
      "source_type": "knowledge_item" | "fact" | "human_attestation",
      "source_id": "…",
      "quote": "the exact span in the source that supports this sentence",
      "locator": { "document_id": "…", "page": 12, "table": 2, "heading_path": ["4", "4.2"] },
      "document_title": "…", "effective_date": "2025-03-01"
    }
  ],
  "support_status": "supported" | "weak" | "unsupported" | "human_authored" | "connective"
}
```

Rules:

- A substantive sentence with no verified source is `unsupported`. It is never hidden, softened or blended into supported text; it is rendered distinctly so the reviewer's attention goes there first.
- `weak` means the quoted span exists in the source but the entailment check was not confident. It is treated like `unsupported` for review purposes but shows the candidate source.
- `human_authored` means a person wrote or substantially rewrote the sentence. That is legitimate; it is attributed to the author and timestamp rather than to a document, and it is visibly different from AI text with a document source.
- `human_attestation` is a source type a reviewer can add to an `unsupported` or `human_authored` sentence, recording "I confirm this is true" with their name and an optional note. It upgrades the sentence to `supported` with a human source. This is how subject-matter verification is captured at sentence level rather than only at answer level.
- Facts cited in a sentence carry their effective date inline in the source, so a sentence resting on a superseded or expired fact is marked `weak` automatically when the fact's status changes, without regenerating the answer.

Editing behaviour. When a person edits an answer, the editor re-aligns segments on save using a sentence-level diff. A sentence whose text is unchanged or changed only slightly (normalised similarity above 0.85) keeps its sources and status. A sentence changed beyond that becomes `human_authored`. New sentences are `human_authored`. Deleted sentences drop their segments. Sources are never silently carried onto text they did not support.

Interface requirements (the colleague designs the visuals; these are the behaviours):

- Inline, not in a footnote list. Each sentence carries an unobtrusive marker at its end. Support status is conveyed by the marker and by a text-decoration style, never by colour alone.
- Hover on a sentence (or focus, by keyboard) shows a popover with the source excerpt, the document title, the location (page, table, section), the document's effective date, and the document kind. No click is required to see this.
- Click on the marker opens the source document in a side pane scrolled to the location, with the quoted span highlighted. The answer editor remains visible.
- A support summary sits at the top of the answer: for example "14 of 16 sentences supported, 2 need review". Clicking it, or pressing a single keyboard shortcut, jumps to the next `unsupported` or `weak` sentence. Reviewers should be able to move through only the sentences that need attention.
- The board and table views show the support summary per question so the bid lead can see which answers carry unreviewed claims without opening them.
- Chat messages render the same way as answers; a message is a list of segments with the same behaviours.

Export. The submission export contains clean prose. A separate internal review export (docx) includes each sentence's sources as Word comments, so a reviewer who works in Word rather than the app still sees the trace.

API. Answers and messages return `segments` rather than free text. `GET /knowledge-items/{id}` returns the item with its `section_locator` and document metadata. `GET /documents/{id}/locate?page=&table=&heading=` returns the rendered section for the side pane, with the source span offsets to highlight. `POST /answers/{id}/segments/{index}/attest` adds a human attestation.

Evaluation. The harness reports, per answer, the share of substantive sentences that are `supported`, the share of `supported` sentences whose quoted span is found verbatim in the cited source, and a human spot-check of twenty sentences per run for whether the cited span actually supports the sentence. The target for the demo is that a reviewer can approve a `covered` question by reading only its unsupported sentences and the gap list.

## Chat threads

The knowledge base is scoped to the organisation, never to a thread. Any document uploaded anywhere becomes available to every thread once indexed. Threads are scoped to a question (the common case) or to a tender (for questions about the tender as a whole, such as which sections carry the most weight). Each assistant message is stored as segments with sources and support status, exactly as answers are, together with the rewritten query and retrieved item identifiers, so any response is auditable sentence by sentence. Conversation history is passed to the synthesis call so instructions such as "make that shorter" or "tailor it for a Scottish health board" work. Mid-conversation uploads run through the ingestion pipeline asynchronously; the thread displays indexing status and new material is included in retrieval as soon as it is ready with no reprocessing of existing items.

Any assistant message can be saved as an answer. This creates a new `answers` version on the thread's question with `author_type = ai`, links the message to it, and moves the question's status to `ai_draft` if it was `not_started`. Without this action, approved text would live in a chat stream and the learning loop would have nothing to attach to.

## Review pipeline

Question status is ordinal and named by who has stood behind the current answer:

1. `not_started`: no answer version exists.
2. `ai_draft`: the current version was produced by the pipeline and not edited.
3. `writer_edited`: a person has edited or rewritten the current version.
4. `sme_verified`: a named subject-matter reviewer has confirmed factual accuracy (for example a clinical safety officer for clinical safety answers, an IG lead for information governance answers).
5. `approved`: the bid lead has signed off the answer for submission.

Any human edit creates a new version, re-aligns segments as described in the traceability section, and moves status to at least `writer_edited`. Moving to `sme_verified` requires that every substantive sentence is `supported` (by a document source or a human attestation); the transition control lists the sentences blocking it. Moving to `approved` additionally requires an empty gap list or an explicit acknowledgement of each remaining gap. Status can move backwards (for example, an SME rejects an answer and it returns to `writer_edited`). Every transition writes an `events` row with actor and timestamp. Colours in the UI are display only and must not be encoded in data.

Per question: assignee, comments, version history with diff between versions, and the event log.

When a tender is marked `submitted`, every `approved` answer is offered for promotion into the library as a `promoted_answer` knowledge item, tagged with buyer, date and outcome when known, and passed through deduplication. This closes the learning loop.

## Information architecture for the front end

This section specifies screens, regions and states. Visual design is the colleague's.

Tenders list (home): every tender with buyer, deadline, days remaining, questions approved over total, words approved over total. Action: new tender.

Tender workspace: header with buyer, deadline, overall progress. Three views over the same question cards:

- Board: cards grouped by status columns. For daily stand-up.
- Table: sortable and filterable by section, weighting, coverage, assignee, status, word count against limit. For the bid lead deciding where effort goes.
- Document: questions in the buyer's `order_index` with current answers inline. For final read-through and export.

Each card shows section and number, truncated question text, word limit, weighting, mandatory flag, coverage, status, assignee, current word count against limit, and the support summary (sentences needing review) for the current answer.

Tender-level actions: upload question pack, draft all, export, mark submitted, open tender-level thread.

Question workspace (opened from a card): three regions, plus a source pane that slides in from the side when a sentence's source is opened, showing the document section with the supporting span highlighted, without hiding the answer editor.

- Question region: full question text, constraints, coverage, status, assignee, section context.
- Answer region: editor holding the current version with live word count against limit, rendered as traceable segments (inline marker per sentence, hover popover with source excerpt and location, click to open the source pane, support summary with jump-to-next-unsupported); version history; status transition controls; comments; attest control on unsupported or human-authored sentences.
- Assistant region: the thread scoped to this question. Each assistant response is rendered as traceable segments with the same hover and click behaviours as the answer editor, followed by the gap list, the fact checklist, and the verbatim option when present. Actions: save as answer, regenerate with instruction, open source.

Library: documents with type, kind, effective date, ingest status and superseded flag; a confirmation prompt after each upload for classification and date; the unpaired-fragments queue for manual pairing; canonical items with variants; facts with effective and expiry dates and supersession chain.

Global upload: available from every screen; documents go to the organisation library regardless of where they were uploaded.

## API surface

REST, JSON, no authentication in the hackathon; `org_id` is taken from a header and defaults to a seeded organisation.

- `POST /documents` (multipart) upload; returns document with `ingest_status`. `GET /documents`, `GET /documents/{id}`, `PATCH /documents/{id}` (override classification, kind, effective date), `GET /documents/{id}/unpaired`, `POST /documents/{id}/pairs` (manual pairing).
- `GET /library/items`, `GET /library/items/{id}` (with variants and `section_locator`), `GET /library/facts`, `GET /documents/{id}/locate?page=&table=&heading=` (rendered section with span offsets for the source pane).
- `POST /tenders`, `GET /tenders`, `GET /tenders/{id}`, `POST /tenders/{id}/question-pack` (multipart; triggers extraction and triage), `POST /tenders/{id}/draft-all`, `POST /tenders/{id}/submit`, `GET /tenders/{id}/export?format=docx|xlsx&mode=submission|review` (review mode includes per-sentence sources as Word comments).
- `GET /tenders/{id}/questions`, `GET /questions/{id}`, `PATCH /questions/{id}` (assignee, status), `POST /questions/{id}/draft` (SSE stream), `GET /questions/{id}/answers` (each with `segments`), `POST /questions/{id}/answers` (human edit creates a version; server re-aligns segments), `POST /answers/{id}/segments/{index}/attest`, `GET /questions/{id}/events`, `POST /questions/{id}/comments`.
- `POST /threads` (tender or question scoped), `GET /threads/{id}`, `POST /threads/{id}/messages` (SSE stream), `POST /messages/{id}/save-as-answer`.

## Evaluation harness

Build this in the first phase, before tuning anything. Without it the project drifts into prompt tinkering.

Data: hold out one past submission entirely. Ingest the others. Treat the held-out submission's questions as the incoming question pack and its answers as ground truth.

Metrics:

- Extraction: pairs extracted over pairs present, checked by hand on one document.
- Retrieval recall at five: for each held-out question, is the corresponding past answer (or its canonical item) in the top five after rerank.
- Coverage accuracy: does the triage label agree with a human label on the held-out questions.
- Draft quality proxy: normalised edit distance between the draft and the held-out approved answer.
- Traceability: share of substantive sentences `supported`; share of cited spans found verbatim in the cited source; human spot-check of twenty sentences per run for whether the span supports the sentence; share of `supported` sentences a human reviewer disagrees with (the number that must be low for reviewers to skip re-checking).
- Latency: per-question draft time and time to a triaged board for the pack.

Toggles: multi-query expansion on or off; topic boosting on or off; rerank on or off; dual embeddings versus answer-only. Report each as a table so the pitch can quote numbers.

## Demo data and confidentiality

Real Tandem submissions contain compliance evidence and must not be loaded into a hackathon product as-is. Generate three synthetic past submissions and a synthetic question pack with Claude, modelled on NHS structure (sections for clinical safety, IG, interoperability, implementation, commercial, social value), with deliberate overlap so deduplication and coverage triage have something to show, plus two synthetic reference documents of the same kind with different dates so supersession is visible. Keep them in `eval/data/`.

## Build order

Phase 1: repository scaffold, docker compose, models and migrations, document upload and Docling parsing, classification and pair extraction on synthetic data, evaluation harness skeleton with extraction and recall metrics.

Phase 2: reference chunking, facts, supersession, deduplication, embedding, hybrid retrieval with fusion and rerank. Recall number in hand.

Phase 3: question pack extraction, coverage triage, draft pipeline producing segments with sources, support verification, gaps, fact checklist and verbatim option, draft-all, SSE streaming. Board populated end to end with support summaries.

Phase 4: threads, save as answer, review states with the traceability gates, segment re-alignment on edit, attestation, versions, events, comments, source pane endpoint, export in both modes. Front end integrates against the API, with the segment renderer and hover popover treated as the first component to build.

Phase 5: promotion on submit, library screens, tuning against the harness, demo script.

## Conventions for Claude Code

- Every LLM call goes through `app/llm/` with a Pydantic output schema and validation; no free-form parsing of model output elsewhere.
- Prompts live as versioned files under `app/llm/prompts/`, not inline strings.
- Thresholds (dedup 0.90, verbatim 0.92, coverage 0.75 and 0.45) live in `config.py` and are read by the harness.
- Tests: pytest; unit tests for fusion, thresholds and state transitions; one integration test that ingests a synthetic submission and asserts recall on its own questions.
- Never use "RAG" as an identifier. Use `retrieval`, `coverage`, `status`.
- Generated text is never stored or returned as a plain string without `segments`. Any code path that produces prose for the user goes through the segment schema and support verification.
- British spelling in user-facing strings.
- Do not build authentication, billing, or portal integrations, however convenient they seem.

## Assumptions to confirm with Puru

- Past submissions contain the buyer's question text alongside each answer in the same file (confirmed).
- Question packs arrive as docx or xlsx, occasionally PDF, typically exported from Atamis (assumption).
- The first demo audience cares about the NHS case specifically, with the general procurement story as the pitch (assumption).
- The colleague's front end will consume REST plus SSE and does not need a GraphQL or websocket interface (assumption).
- A single organisation is seeded for the hackathon; there is no organisation switching in the UI (assumption).

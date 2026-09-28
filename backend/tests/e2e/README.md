# Offline end-to-end test of the MVP steps

`test_mvp_steps.py` walks the six MVP steps of `CLAUDE.md` on the synthetic data in
`eval/data`, through the real API and the real job handlers, with no network and no model.
Step 6 (the evaluation report) belongs to the harness and is not exercised here.

```sh
cd /path/to/Tenders
.venv/bin/python -m pytest backend/tests/e2e -q -p no:warnings          # about 3 seconds
.venv/bin/python -m pytest backend/tests -q -p no:warnings -k e2e -s    # with the coverage print
```

What the one test proves, in order:

1. Upload the three past submissions and the two ISO 27001 certificates with `POST /documents`,
   run the `ingest_document` jobs to `ready`, and check the document list (counts of pairs,
   chunks and facts; every pair `text_verified`; the unpaired queue empty), a document's
   sections, the confirmation of each classification, the automatic supersession of the 2024
   certificate once the 2025 one is confirmed (pointer, excluded items, no pending decision),
   the deduplication clusters and the fact chain with keys.
2. Create a tender, upload the xlsx question pack, run `extract_questions` and the chained
   `triage_tender`, and check that every card left `unknown` is zero, that the coverage
   distribution has covered and new questions, and that questions the manifest marks `new` are
   triaged `new`.
3. Stream a draft for the covered ISO 27001 question over `POST /questions/{id}/draft`: event
   order, wire indices equal to persisted indices, `pending` on the wire only, support events
   whose offsets resolve through `GET /sections/{id}?start=&end=` to the quoted text, a fact
   cited, the verbatim offer for a repeated question, and `done` equal to the persisted answer.
4. Thread message, save-as-answer, human edit with unchanged text (statuses kept,
   `writer_edited`), human edit with one changed and one added sentence (re-verified, then
   `human_authored`), attest, dispute, rewrite and attest, acknowledge a gap, compliance class,
   evidence, comment, assignment, and the transitions to `sme_verified` and `approved` with
   `allowed_transitions` consulted before each; then the event log.
5. The table view filtered client-side, the submission docx in the buyer's order with the
   approved answer written in full and `[No approved answer]` everywhere else.
6. The fact-expiry path: a cited fact expires, `run_expiry_sweep` flags the answer, export
   returns 409, attestation clears it and export succeeds.

## The heuristic fake

`tests/fakes/heuristic_llm.py` holds `HeuristicFakeLLM`, a subclass of `app.llm.client.FakeLLM`
that implements the `LLMClient` protocol (`parse` and `stream_text`). A response registered
with `register(name, ...)` still wins; every unregistered call is answered from the user
message by rules:

| Prompt | Rule |
| --- | --- |
| `classify_document` | `Buyer:` line plus `Submitted by ... on <date>` means `past_submission` with buyer and dates; `certificate` plus `ISO/IEC 27001` means `reference` / `iso_27001` with the effective date from `Certificate issued <date>`. |
| `extract_pairs` | Parses the `<<< section id=... >>>` labels and recognises the three synthetic layouts: question and answer in adjacent cells of one row, a numbered question heading with the answer beneath, and `Question n.m` paragraphs paired with the boxed answer rows that follow. Anchors are the first and last six words, copies are exact, topics come from keyword counts and facts from regular expressions over the generator's dated sentences (ISO 27001, Cyber Essentials Plus, DSPT, named officers, safety case version, headcount, company number, insurance limits). |
| `chunk_annotate` | Topics by keyword; the certificate sentence of a reference chunk becomes an `iso_27001` fact with issue and validity dates. |
| `extract_questions` | One question per spreadsheet row, columns mapped by the header cells in the heading path (Section, Number, Question, Word limit, Weighting, Response type, Mandatory). |
| `coverage_judgement` | `covered` when a candidate shares at least half of the question's content words, `partial` from a fifth, otherwise `new`, with a British gap summary. |
| `query_rewrite` | Echoes the latest turn; an instruction about the draft ("make it shorter") retrieves with the tender question. |
| `entailment` | `supported` when the located spans share at least two content words with the sentence, else `weak`. |
| `synthesis` | NDJSON per the plan: a connective opener, sentences copied verbatim from the top candidates (one segment deliberately holds two sentences so conformance splits it), each citing its item and any non-expired fact whose statement it contains; then `gaps` (the question's content words no candidate covers) and `fact_checklist`. A "shorter" instruction keeps fewer sentences; a pricing question yields gaps only. |
| `synthetic_answers` | Not handled: the call raises. Generate the data with the template writer. |

The fixture `heuristic_llm` (in `e2e/conftest.py`) swaps it in through the same mechanism as
the shared `fake_llm` fixture: `LLM_PROVIDER=fake` through `settings_override` and
`reset_llm()`, with the client module's `FakeLLM` name pointed at the subclass first.

Limits. It is not a model. It knows the structures and fact sentences of
`eval/synthetic/content.py` and `documents.py`; it will extract little from a real
submission, its topic labels are keyword counts (a DSPT answer that mentions staff training
also gets `training_and_support`), its coverage judgement is word overlap (four of the
twenty-five counterpart questions come out `new` in template mode) and its entailment accepts
any sentence that shares two content words with its span, so it never catches a changed
number or a negation. Its purpose is to prove the plumbing between the modules, end to end,
deterministically, in a few seconds.

## Running the same walk-through against the real provider

Before the demo, run the test once with Claude and real embeddings as a smoke test of the
prompts and schemas. The fixtures below force the fakes, so run the pipeline through the
application instead of through pytest:

1. Set the environment: `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...`,
   `EMBEDDING_PROVIDER=openai` with `OPENAI_API_KEY=...` (or `voyage` with `VOYAGE_API_KEY`),
   and a `DATABASE_URL` for a scratch Postgres with pgvector (for example the compose
   database). Optionally regenerate the corpus with Claude-written prose:
   `.venv/bin/python -m eval.generate_synthetic --out eval/data --mode claude`.
2. Start the API and the worker (`docker compose up`, or `uvicorn app.main:app --workers 1` and
   `python -m app.worker` from `backend/` after `alembic upgrade head` and
   `python -m app.db.seed`).
3. Replay steps 1 to 5 with `curl` or the front end in the order the test uses: upload the five
   library files and poll `GET /documents/{id}` until `ready`; `POST /documents/{id}/confirm`
   for each, the 2024 certificate before the 2025 one; create the tender, upload the pack, poll
   `GET /jobs/{id}` following `next_job_id`; `POST /questions/{id}/draft` on a covered card and
   read the NDJSON; then the review actions and `GET /tenders/{id}/export`.
4. Compare what you see with the assertions in `test_mvp_steps.py`: every pair `text_verified`,
   no card left `unknown`, every support event's offsets resolving through `GET /sections`,
   `pending` never in a stored answer.

The pytest fixtures in `tests/conftest.py` force `LLM_PROVIDER=fake` before the app imports,
which is deliberate: the test suite must never need a key. Running the E2E module against the
real provider therefore means the manual replay above, not `pytest` with the environment set.

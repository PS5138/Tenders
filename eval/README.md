# Evaluation

`eval/data/` holds the synthetic data the harness runs on: the generator's output (three synthetic past submissions, a synthetic question pack, two reference documents of one unkeyed kind with different dates) and `ground_truth.json`, which maps each held-out question to the ingested item or items that answer it; `eval/data/fixtures/fixture_document.json` is the pre-parsed fixture document the seed inserts so `GET /fixtures/answer` and `GET /sections/{id}` work on day one (no parsing at seed time, no knowledge items, so it never enters retrieval). `eval/labels/` accumulates human labels across runs (coverage labels, the twenty-sentence spot-checks, disputes) so they are never redone; metrics that need labels report `not labelled` rather than zero when none exist. `eval/reports/` receives one `<timestamp>.md` and its `.json` twin per harness run; MVP step 6 is opening the latest `.md`. Hackathon numbers come from synthetic data and are labelled as such wherever quoted; no Tandem submission is ever loaded.

Layout:

| Path | What it is |
| --- | --- |
| `generate_synthetic.py`, `synthetic/` | the synthetic data generator (see `data/README.md`) |
| `harness.py` | the evaluation harness and its CLI |
| `toggles.py` | the three retrieval toggles, applied by monkeypatching `app.retrieve` at run time |
| `metrics/` | one module per metric family: extraction, retrieval recall, coverage, draft quality, traceability, latency; `resolution.py` maps a ground-truth counterpart to a stored item and its cluster; `labels.py` reads the human labels |
| `report.py` | writes `<run>.md`, its `.json` twin and `<run>.spotcheck.json` |
| `prompts/` | the harness's own versioned prompts: `judge.v1.md` (draft quality) and `rerank.v1.md` (the rerank toggle) |
| `labels/` | human labels; `labels/README.md` gives the two file shapes |
| `reports/` | one report per run (per-run artefacts, not committed) |

## Running the harness

Offline, with the fake providers (the heuristic fake LLM from `backend/tests/fakes` and the hash embeddings), against a throwaway Postgres started from the `pgserver` package and deleted afterwards:

```sh
cd /path/to/Tenders
.venv/bin/python -m eval.harness --fake --data eval/data --out eval/reports
```

The run takes a few seconds. It creates a fresh schema (`alembic upgrade head` and the seed), ingests every non-held-out synthetic document through the real `ingest_document` handler (dispatched in-process by the worker's `run_once`), confirms each document so supersession fires, creates a tender, ingests the question pack through `extract_questions` and `triage_tender`, drafts every question through the real pipeline synchronously (pricing questions are never drafted and are reported as skipped), computes the metrics and writes three files:

- `eval/reports/<run>.md`: the report (every metric, the toggle table, the coverage floor, providers, models, prompt versions, all labelled as synthetic data);
- `eval/reports/<run>.json`: the same figures as data;
- `eval/reports/<run>.spotcheck.json`: twenty (sentence, source) pairs sampled deterministically for a person to label.

Options:

| Flag | Effect |
| --- | --- |
| `--fake` | force `LLM_PROVIDER=fake` and `EMBEDDING_PROVIDER=fake` for this process |
| `--db URL` | use an existing Postgres with pgvector instead of a throwaway one; the schema is migrated to head and seeded, existing rows are left alone; use a scratch database, never the compose one |
| `--toggle NAME=VALUE` | one retrieval toggle; repeatable: `topic_list=on\|off`, `vector_list=max\|answer_only`, `llm_rerank=on\|off` |
| `--matrix` | run every on/off combination of the three toggles (eight tender passes over one ingested library) and print one table row per configuration |
| `--labels DIR` | where the human labels live (default `eval/labels`) |
| `--no-judge` | skip the LLM judge calls |
| `--rerank-pool N` | how many fused candidates the rerank sees before the cut to eight (default 20) |
| `--verbose` | log every stage |

From Python, `eval.harness.run(data_dir, out_dir, *, database_url=None, toggles=None, labels_dir="eval/labels", today=None)` returns a `Report` with the figures (`report.data`, `report.metric("retrieval", "value")`) and the three paths. `toggles` is a `Toggles`, a list of them (one table row each) or `None` for the baseline; `today` fixes the run's clock so a test gets a predictable report name.

Toggles change nothing under `backend/app`: `eval/toggles.py` documents exactly which attributes of `app.retrieve.search` and `app.retrieve.coverage` it swaps for the duration of a tender pass and restores afterwards. `llm_rerank=on` adds one schema-validated FAST-model call over the fused, collapsed candidates before the cut to eight.

### With the fake providers

The plain `FakeLLM` cannot synthesise, so with `LLM_PROVIDER=fake` the harness installs `tests.fakes.heuristic_llm.HeuristicFakeLLM` (the rule-based model of the offline end-to-end test) and registers fixed answers for its own prompts: the judge returns a fixed score of 3 and the report marks it "fake provider"; the rerank orders candidates by word overlap so the toggle's plumbing is exercised. Numbers from the fake provider prove the plumbing and the metric arithmetic, not quality; the retrieval numbers also rest on hash embeddings, so `best_vec` is not on the cosine scale a real embedding gives.

### With the real provider

Set the environment (or `.env`) and drop `--fake`:

```sh
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... \
EMBEDDING_PROVIDER=openai OPENAI_API_KEY=... \
.venv/bin/python -m eval.harness --data eval/data --out eval/reports --matrix
```

The harness refuses to start when the configured provider has no key. Optionally regenerate the corpus with Claude-written prose first (`python -m eval.generate_synthetic --out eval/data --mode claude`); `ground_truth.json` stays correct whichever writer produced the prose. A real run makes roughly: one classification per document, one extraction call per window, one coverage judgement per question above the floor, one synthesis and one entailment call per drafted question, one judge call per question with a held-out answer, and with `llm_rerank=on` one rerank call per retrieval (about twice per question, triage and draft). `--matrix` multiplies the tender passes by eight.

## Adding labels

Human labels live in `eval/labels/` and accumulate across runs. After a run, open the newest `eval/reports/<run>.spotcheck.json`, judge each of the twenty rows (`supports: true|false`, your name in `labelled_by`) and append them to `eval/labels/spot_checks.json` under `checks`; add a row per question to `eval/labels/coverage_labels.json` where you disagree with, or want to confirm, the triage label. The next run prints the spot-check share, the share of supported sentences a human disputed and the coverage agreement instead of `not labelled`. `eval/labels/README.md` gives both file shapes and how rows are matched to a run.

## Tests

`backend/tests/test_eval_harness_run.py` runs the harness twice on `eval/data` against a database created on the test suite's pgserver (a baseline run, then a two-configuration toggle run) and checks the report's sections, the recall over resolved counterparts, the fidelity target, `not labelled` for the human metrics, the twenty-row spot-check file and that the toggles patch and unpatch cleanly:

```sh
.venv/bin/python -m pytest backend/tests/test_eval_harness_run.py -q -p no:warnings
```

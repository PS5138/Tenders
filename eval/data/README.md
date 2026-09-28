# Synthetic evaluation data

Everything in this directory except `fixtures/` is written by the synthetic data generator:

```sh
.venv/bin/python -m eval.generate_synthetic --out eval/data            # mode chosen by LLM_PROVIDER
.venv/bin/python -m eval.generate_synthetic --out eval/data --mode template
.venv/bin/python -m eval.generate_synthetic --out eval/data --mode claude --seed 0
```

or from Python, `eval.generate_synthetic.generate(out_dir, seed=0, mode=None)`.

The data is synthetic. The supplier (Northlight Health Informatics Ltd), its people, its
certificates, the buyers and every tender reference are invented. No real submission is
loaded, and any number produced from this data must be labelled as coming from synthetic
data (see the benchmark policy in `../../CLAUDE.md`).

## Two writers

- **template** (used when `LLM_PROVIDER=fake`, and by the tests): a deterministic writer picks
  one phrasing per sentence slot from the sentence pools in `eval/synthetic/content.py`, seeded
  by `--seed` and the question, so the same seed always gives the same files.
- **claude** (used when `LLM_PROVIDER=anthropic`): one schema-validated call per past submission
  through `app.llm.get_llm().parse` with `MODEL_MAIN` and the prompt
  `backend/app/llm/prompts/synthetic_answers.v1.md`. The call receives a content brief built from
  the same content module; the model writes the prose. Dated fact sentences are pinned verbatim,
  headings and list markers are stripped, and any question the model skips falls back to the
  template writer (recorded in `manifest.json` under `template_fallback_concept_ids`).

In both modes the document structure, the question pack and `ground_truth.json` come from the
same specification, so the ground truth is correct whichever writer produced the prose.

## Files

| File | Role | Notes |
| --- | --- | --- |
| `past_submission_westmoor_icb_2024.docx` | past submission, ingested | layout: question and answer in adjacent table cells; submitted 14 June 2024 |
| `past_submission_harbourside_fT_2025.docx` | past submission, ingested | layout: question as heading, answer beneath; submitted 21 February 2025 |
| `past_submission_northern_fells_2025_heldout.docx` | past submission, **held out** | layout: numbered form with a boxed answer cell; submitted 18 July 2025 |
| `reference_iso27001_certificate_2024.docx` | reference, `iso_27001` | states "Certificate issued 12 March 2024" in its first paragraph |
| `reference_iso27001_certificate_2025.docx` | reference, `iso_27001` | states "Certificate issued 9 March 2025"; supersedes the 2024 certificate once both are confirmed |
| `question_pack_northern_fells_2025.xlsx` | question pack | one sheet, columns Section, Number, Question, Word limit, Weighting, Response type, Mandatory; about 43 rows |
| `ground_truth.json` | held-out mapping | see below |
| `manifest.json` | what was generated | per file: role, layout, expected classification, pairs with concept ids, near-identical links, writer and model |
| `fixtures/fixture_document.json` | seed fixture | not written by the generator; the pre-parsed document the seed inserts |

Each past submission holds 14 question-answer pairs across the sections of an NHS-style pack
(clinical safety with DCB0129 and a named Clinical Safety Officer, information governance with
DSPT status and a Data Protection Officer, information security with ISO 27001 and Cyber
Essentials Plus certificate numbers and dates, interoperability, implementation, training and
support, commercial without prices, social value, company and experience). Answers are plain
paragraphs with no headings of their own, so every answer lies within one parsed section.

## Deliberate overlap

- Five questions appear in both ingested submissions with near-identical answers (the second
  is a one-slot rewrite of the first), so deduplication has clusters to find: DCB0129, DSPT,
  ISO 27001, interoperability standards and the Carbon Reduction Plan. `manifest.json` lists
  them under `near_identical_to`.
- Twelve of the held-out submission's fourteen questions have a counterpart in the ingested
  submissions; two (DCB0160 support, data retention) do not, so the `new` bucket of the
  draft-quality metric has reference answers to score against.
- The certificates cited change with the submission date (ISO 27001 and Cyber Essentials Plus
  renew, the clinical safety case report moves from version 2.6 to 3.2, headcount grows), so
  fact supersession has pairs to evaluate between the two ingested submissions and between the
  two reference documents.

## Question pack and `ground_truth.json`

The pack is modelled on the held-out submission: its fourteen questions in the pack's own
numbering, plus further questions with a counterpart in the ingested submissions (but no
held-out answer), pricing and yes/no questions, an attachment request, and several questions
with no counterpart at all.

`ground_truth.json` has one entry per pack row, keyed by `section` and `number`:

```json
{
  "section": "3. Information security",
  "number": "3.1",
  "question_text": "...",
  "concept_id": "is_iso27001",
  "response_type": "free_text",
  "label": "counterpart",
  "counterparts": [
    {"submission_filename": "past_submission_westmoor_icb_2024.docx", "question_text": "...", "concept_id": "is_iso27001"},
    {"submission_filename": "past_submission_harbourside_fT_2025.docx", "question_text": "...", "concept_id": "is_iso27001"}
  ],
  "held_out_answer": "..."
}
```

`label` is `counterpart` when at least one ingested item answers the question and `new` when
none does. `counterparts` names the ingested item by the file it came from and the question
text as it appears in that file, which is what the harness matches against
`knowledge_items.question_text` (after normalisation) to find the item and its cluster for
recall at eight. `held_out_answer` is the held-out submission's answer, or null for pack rows
the held-out submission did not answer. Yes/no questions that a free-text answer covers (DSPT
status, Cyber Essentials Plus) point at that answer. Pricing questions are always `new`; the
pipeline never drafts them.

Human labels (coverage labels, spot-checks, disputes) do not belong here; they accumulate in
`../labels/`.

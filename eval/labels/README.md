# Human labels for the evaluation harness

Labels accumulate here across runs so they are never redone (CLAUDE.md, "Evaluation
harness"). The harness reads both files at the start of a run; a metric that finds no label
for the run's questions or sentences reports `not labelled`, never zero. Both files are
optional and both are plain JSON a person edits by hand.

## `coverage_labels.json`: the human coverage label per pack question

One row per question of the synthetic pack, keyed by the pack's `section` and `number`
exactly as `ground_truth.json` spells them. `coverage` is the label a bid writer would give
after reading the library: `covered` (a complete answer can be drafted from the library),
`partial` (the substance is there but a material part is missing) or `new` (nothing in the
library addresses the substance).

```json
{
  "labels": [
    {
      "section": "3. Information security",
      "number": "3.1",
      "coverage": "covered",
      "labelled_by": "Puru",
      "at": "2026-09-28",
      "note": "Both ISO 27001 answers cover number, scope and date."
    }
  ]
}
```

`labelled_by`, `at` and `note` are free text for the record. The coverage metric reports
agreement between the triage label and these rows, with a confusion table and the list of
disagreements; questions without a row are not counted.

## `spot_checks.json`: the twenty-sentence spot check and disputes

Every run writes `eval/reports/<run>.spotcheck.json` with twenty (sentence, source) pairs
sampled deterministically from the run's drafts, `supports` left `null`. To label a run,
read each sentence against its quoted span (the stored section text at `locator.start` to
`locator.end`, which is what the source pane highlights), set `supports` to `true` when the
span establishes the sentence and `false` when it does not, add your name, and append the
judged rows to this file under `checks`:

```json
{
  "checks": [
    {
      "section": "1. Clinical safety",
      "number": "1.3",
      "sentence": "A hazard with a residual rating above three cannot be released without a written justification from the Clinical Safety Officer.",
      "quote": "A hazard with a residual rating above three cannot be released without a written justification from the Clinical Safety Officer.",
      "source_type": "knowledge_item",
      "document_title": "past_submission_harbourside_fT_2025.docx",
      "support_status": "supported",
      "supports": true,
      "labelled_by": "Puru",
      "note": ""
    }
  ]
}
```

Rows are matched to a later run by `section`, `number`, `sentence` and `quote` after the
shared text normalisation (`app.ingest.normalise.normalise`), so the ids that change between
runs do not matter and a label stays valid for as long as the pipeline produces the same
sentence from the same span. Rows whose `supports` is still `null` are ignored. Extra keys
copied from the sample file (`segment_index`, `locator`, `tier`, `answer_id`) are kept for
the record and ignored by the matcher.

Two metrics read this file:

- the spot check itself: the share of labelled pairs whose span supports the sentence;
- the share of `supported` sentences a human disputed: among the run's sentences that
  support verification marked `supported` and that carry a label, the share labelled
  `supports: false`. This is the number the plan says must be low for reviewers to skip
  re-checking supported sentences.

A dispute recorded in the product (the dispute control on a supported sentence) is a
different record: it lives on the answer's segment and in the event log of that tender. The
harness runs on a throwaway database, so only these files carry human judgement across runs.

## Adding labels

1. Run the harness (`python -m eval.harness --fake ...` offline, or with the real provider).
2. Open the newest `eval/reports/<run>.spotcheck.json`, judge the twenty rows and append
   them here; add or update coverage rows for any question you have an opinion on.
3. Run the harness again: the report now prints the spot-check share, the disputed share and
   the coverage agreement instead of `not labelled`.

Everything here is a judgement about synthetic data and carries the same label.

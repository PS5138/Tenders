"""Report writer: ``eval/reports/<run>.md``, its ``.json`` twin and the spot-check sample.

The JSON holds every figure the harness computed; the Markdown is rendered from that same
dict, so the two never disagree. The synthetic-data label sits at the top of the Markdown and
in the JSON (``synthetic_data``), as the plan's benchmark policy requires. Metrics that need
human labels arrive as the string ``not labelled`` and are printed as such.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.metrics.labels import NOT_LABELLED

SYNTHETIC_BANNER = (
    "**SYNTHETIC DATA.** Every figure in this report was produced from the synthetic corpus "
    "under `{data_dir}` (generator seed {seed}, writer `{mode}`; the supplier, its people, "
    "its certificates and the buyers are invented). Quote these numbers only as "
    "synthetic-data numbers. No real submission was loaded; real past tenders join the "
    "benchmark only as design partners contribute them under agreement (CLAUDE.md, "
    "benchmark policy)."
)

SPOT_CHECK_INSTRUCTIONS = (
    "For each row read the sentence and the quoted span (the section text at the locator "
    "offsets) and set supports to true when the span establishes the sentence, false when it "
    "does not; add your name in labelled_by. Copy the judged rows into "
    "eval/labels/spot_checks.json under checks."
)


@dataclass
class Report:
    """What ``harness.run`` returns: the figures and where they were written."""

    run_id: str
    data: dict[str, Any]
    markdown_path: Path
    json_path: Path
    spotcheck_path: Path | None

    @property
    def configurations(self) -> list[dict[str, Any]]:
        return list(self.data.get("configurations", []))

    def configuration(self, label: str | None = None) -> dict[str, Any]:
        """The configuration named ``label``, or the first (baseline) one."""
        if label is None:
            return self.configurations[0]
        for configuration in self.configurations:
            if configuration["label"] == label:
                return configuration
        raise KeyError(label)

    def metric(self, group: str, *path: str, label: str | None = None) -> Any:
        value: Any = self.configuration(label)["metrics"][group]
        for key in path:
            value = value[key]
        return value


# --- Formatting helpers -----------------------------------------------------------------------


def pct(value: Any) -> str:
    if value == NOT_LABELLED:
        return NOT_LABELLED
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def num(value: Any, digits: int = 3) -> str:
    if value == NOT_LABELLED:
        return NOT_LABELLED
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.{digits}f}"


def ratio(hits: Any, total: Any, value: Any) -> str:
    if not total:
        return "n/a (0)"
    return f"{hits}/{total} ({pct(value)})"


def secs(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f} s"


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(str(h) for h in headers) + " |"]
    lines.append("|" + "|".join(" --- " for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    return lines


def _cell(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _strata(block: Mapping[str, Any], digits: int = 3) -> str:
    if not block:
        return "n/a"
    return "; ".join(
        f"{key}: {num(entry['mean'], digits)} (n={entry['n']})" for key, entry in block.items()
    )


def _code(values: Iterable[str]) -> str:
    return ", ".join(f"`{value}`" for value in values)


# --- Sections -----------------------------------------------------------------------------------


def _render_run(data: Mapping[str, Any]) -> list[str]:
    run = data["run"]
    providers = data["providers"]
    models = data["models"]
    thresholds = data["thresholds"]
    labels = data["labels"]
    lines = ["## Run", ""]
    lines += [
        f"- Run id: `{run['id']}`; started {run['timestamp']}; "
        f"duration {secs(run['duration_seconds'])}.",
        f"- Database: {run['database']}.",
        f"- Data: `{run['data_dir']}`; reports: `{run['out_dir']}`; labels: "
        f"`{labels['labels_dir']}` ({labels['coverage_labels']} coverage label(s), "
        f"{labels['spot_checks']} judged spot-check row(s)).",
        f"- Actor recorded on events: `{run['actor']}`. "
        f"LLM judge: {'on' if run['judge_enabled'] else 'off'}.",
        "",
        "### Providers and models",
        "",
    ]
    embedding = (
        f"{providers['embedding_model']} ({providers['embedding_dimension']} dimensions)"
    )
    lines += table(
        ["Component", "Provider", "Client or model"],
        [
            ["LLM", providers["llm_provider"], providers["llm_client"]],
            ["Embeddings", providers["embedding_provider"], embedding],
            [
                "Main model (synthesis, extraction, rewrite)",
                providers["llm_provider"],
                models["main"],
            ],
            [
                "Fast model (classification, coverage, entailment, judge, rerank)",
                providers["llm_provider"],
                models["fast"],
            ],
        ],
    )
    lines += ["", "### Prompt versions", ""]
    lines += table(
        ["Prompt", "Version"],
        [[name, version] for name, version in sorted(data["prompt_versions"].items())],
    )
    lines += ["", "### Thresholds read from `config.py`", ""]
    lines += table(["Setting", "Value"], [[key, value] for key, value in thresholds.items()])
    lines += [
        "",
        f"The coverage floor used by triage in this run was **{thresholds['coverage_floor']}** "
        "(highest step-3 vector score among the fused top eight, cosine scale).",
        "",
        "### Toggle configurations",
        "",
    ]
    for configuration in data["configurations"]:
        suffix = " (baseline)" if configuration["baseline"] else ""
        lines.append(f"- `{configuration['label']}`{suffix}")
    if run.get("llm_calls"):
        lines += ["", "### LLM calls by prompt (fake provider only)", ""]
        lines += table(
            ["Prompt", "Calls"],
            [[name, count] for name, count in sorted(run["llm_calls"].items())],
        )
    return lines


def _render_ingestion(data: Mapping[str, Any]) -> list[str]:
    ingestion = data["ingestion"]
    lines = ["## Ingestion of the library", ""]
    lines.append(
        f"{len(ingestion['documents'])} document(s) ingested through `ingest_document` in "
        f"{secs(ingestion['seconds'])}, each confirmed afterwards so the supersession gate ran "
        "as it does for a user. Held out and never ingested: "
        f"`{data['data']['held_out_submission']}`."
    )
    lines.append("")
    lines += table(
        [
            "File", "Role", "Type", "Kind", "Effective", "Status", "Pairs", "Chunks", "Facts",
            "Confirmed", "Superseded by", "Seconds",
        ],
        [
            [
                row["filename"], row["role"], row["doc_type"], row["doc_kind"],
                row["effective_date"], row["ingest_status"], row["items"].get("qa_pair", 0),
                row["items"].get("chunk", 0), row["facts"], row["classification_confirmed"],
                row["superseded_by"], row["seconds"],
            ]
            for row in ingestion["documents"]
        ],
    )
    supersession = ingestion["supersession"]
    lines += ["", "### Supersession", ""]
    if supersession["superseded_documents"]:
        for entry in supersession["superseded_documents"]:
            lines.append(f"- `{entry['filename']}` superseded by `{entry['superseded_by']}`.")
    else:
        lines.append("- No document superseded.")
    if supersession["decisions"]:
        lines.append("")
        lines += table(
            ["Kind", "Document A", "Document B", "Reason", "Decision", "Superseding"],
            [
                [
                    d["doc_kind"], d["document_a"], d["document_b"], d["reason"],
                    d["decision"], d["superseding"],
                ]
                for d in supersession["decisions"]
            ],
        )
        lines.append("")
    lines.append(
        f"- Facts: {supersession['facts_total']} stored, "
        f"{supersession['facts_superseded']} superseded by a later fact."
    )
    if ingestion["failures"]:
        lines += ["", "### Job failures", ""]
        for failure in ingestion["failures"]:
            lines.append(f"- `{failure['kind']}` {failure['job_id']}: {failure['error']}")
    return lines


def _render_extraction(data: Mapping[str, Any]) -> list[str]:
    extraction = data["extraction"]
    lines = ["## Extraction", ""]
    lines += table(
        [
            "Submission", "Layout", "Present", "Extracted", "Matched", "Extracted/present",
            "Matched/present", "Unverified items", "Fragments",
        ],
        [
            [
                row["filename"], row["layout"], row["pairs_present"], row["pairs_extracted"],
                row["pairs_matched"], pct(row["extracted_over_present"]),
                pct(row["matched_over_present"]), row["unverified_items"],
                row["unresolved_fragments"],
            ]
            for row in extraction["submissions"]
        ],
    )
    totals = extraction["totals"]
    fidelity = extraction["fidelity"]
    extracted = ratio(
        totals["pairs_extracted"], totals["pairs_present"], totals["extracted_over_present"]
    )
    matched = ratio(
        totals["pairs_matched"], totals["pairs_present"], totals["matched_over_present"]
    )
    all_items = ratio(fidelity["items_verified"], fidelity["items_total"], fidelity["share"])
    pairs_only = ratio(fidelity["pairs_verified"], fidelity["pairs_total"], fidelity["pairs_share"])
    verdict = "met" if fidelity["meets_target"] else "NOT met"
    lines += [
        "",
        f"- Pairs extracted over pairs present: **{extracted}**; matched to the manifest's "
        f"question text: {matched}.",
        f"- Extraction fidelity (`text_verified` share): **{all_items}** over all items; pairs "
        f"alone {pairs_only}; target {fidelity['target']}: **{verdict}**.",
        f"- {extraction['note']}",
    ]
    return lines


_HEADLINE_HEADERS = [
    "Configuration", "Recall@8", "Covered / partial / new", "New: floor / judgement",
    "Supported share", "Verbatim / near (of cited spans)", "Edit distance (mean)",
    "Judge (mean, 1-5)", "Draft p50 / p95", "Time to triaged board", "Rerank calls",
]


def _headline_row(configuration: Mapping[str, Any]) -> list[Any]:
    metrics = configuration["metrics"]
    retrieval, coverage = metrics["retrieval"], metrics["coverage"]
    quality, trace = metrics["draft_quality"], metrics["traceability"]
    latency = metrics["latency"]
    distribution = coverage["distribution"]
    new_sources = coverage["new_by_label_source"]
    tiers = trace["span_tiers"]
    draft = latency["draft_seconds"]
    rerank = configuration.get("rerank")
    return [
        f"`{configuration['label']}`",
        ratio(retrieval["hits"], retrieval["total"], retrieval["value"]),
        f"{distribution.get('covered', 0)} / {distribution.get('partial', 0)} / "
        f"{distribution.get('new', 0)}",
        f"{new_sources['floor']} / {new_sources['llm']}",
        pct(trace["supported_share"]),
        f"{pct(tiers['verbatim_share_of_cited'])} / {pct(tiers['near_share_of_cited'])}",
        num(quality["edit_distance"]["mean"]),
        num(quality["judge"]["mean"], 2),
        f"{secs(draft['p50'])} / {secs(draft['p95'])}",
        secs(latency["time_to_triaged_board_seconds"]),
        rerank["calls"] if rerank else "off",
    ]


def _render_toggle_table(data: Mapping[str, Any]) -> list[str]:
    configurations = data["configurations"]
    title = "## Toggle table" if len(configurations) > 1 else "## Headline figures"
    lines = [title, ""]
    if len(configurations) > 1:
        lines.append(
            "One row per retrieval configuration; the library was ingested once and each "
            "configuration ran its own tender pass (extraction, triage, every question drafted)."
        )
        lines.append("")
    lines += table(_HEADLINE_HEADERS, [_headline_row(c) for c in configurations])
    lines += [
        "",
        "Recall@8 counts a hit when the item, its canonical or a variant of a ground-truth "
        "counterpart is among the eight candidates synthesis received. The judge column is a "
        "fixed score under the fake provider (see each configuration's draft-quality section).",
    ]
    return lines


def _render_retrieval(retrieval: Mapping[str, Any]) -> list[str]:
    triage = retrieval["triage_candidates"]
    lines = ["### Retrieval recall at eight", ""]
    lines += [
        "- Recall@8 over the draft's candidates: "
        f"**{ratio(retrieval['hits'], retrieval['total'], retrieval['value'])}** "
        "(held-out questions with at least one resolved counterpart).",
        "- The same over the triage candidates: "
        f"{ratio(triage['hits'], triage['total'], triage['value'])}.",
        f"- Counterparts resolved by: {retrieval['resolution_methods'] or 'none'}.",
    ]
    unresolved = retrieval["unresolved_counterparts"]
    if unresolved:
        lines.append(
            f"- **Unresolved counterparts ({len(unresolved)})**, excluded from the denominator:"
        )
        for entry in unresolved:
            lines.append(
                f"  - {entry['section']} {entry['number']}: `{entry['submission_filename']}` "
                f"— {entry['question_text']}"
            )
    else:
        lines.append("- Unresolved counterparts: none.")
    misses = [row for row in retrieval["per_question"] if row.get("hit") is False]
    if misses:
        listed = ", ".join(
            f"{row['section']} {row['number']} ({row['coverage']})" for row in misses
        )
        lines.append(f"- Misses: {listed}.")
    return lines


def _render_coverage(coverage: Mapping[str, Any]) -> list[str]:
    distribution = coverage["distribution"]
    new_sources = coverage["new_by_label_source"]
    proxy = coverage["ground_truth_proxy"]
    lines = ["### Coverage", ""]
    lines += [
        f"- Distribution: covered {distribution.get('covered', 0)}, partial "
        f"{distribution.get('partial', 0)}, new {distribution.get('new', 0)}, unknown "
        f"{distribution.get('unknown', 0)}.",
        f"- `new` from the floor: {new_sources['floor']}; `new` from the judgement: "
        f"{new_sources['llm']} (label sources over all questions: {coverage['label_sources']}).",
        f"- Coverage floor: {coverage['coverage_floor']} (recorded in coverage_detail as "
        f"{coverage['coverage_floor_in_details']}).",
        "- Agreement with human labels (`coverage_labels.json`): "
        f"**{_human_accuracy(coverage['human_accuracy'])}**.",
        "- Ground-truth proxy: counterpart questions triaged covered or partial "
        f"{proxy['counterpart_covered_or_partial']}/{proxy['counterpart_total']}; new questions "
        f"triaged new {proxy['new_judged_new']}/{proxy['new_total']}; agreement "
        f"{pct(proxy['agreement'])}. {proxy['note']}",
        "- best_vec by ground-truth label (for re-basing the floor): "
        f"{_best_vec(coverage['best_vec_by_ground_truth'])}.",
        f"- best_vec by triage label: {_best_vec(coverage['best_vec_by_coverage'])}.",
    ]
    return lines


def _render_quality(quality: Mapping[str, Any]) -> list[str]:
    edit = quality["edit_distance"]
    judge = quality["judge"]
    lines = ["### Draft quality against the held-out answer", ""]
    lines += [
        f"- {quality['with_held_out_answer']} question(s) have a held-out answer; "
        f"{quality['scored']} drafted and scored.",
        f"- Normalised edit distance: mean **{num(edit['mean'])}** (n={edit['n']}; "
        f"{edit['note']}). By coverage: {_strata(edit['by_coverage'])}. By ground-truth label: "
        f"{_strata(edit['by_ground_truth'])}.",
        f"- LLM judge ({judge['provider']} provider, model `{judge['model']}`, prompt "
        f"`{judge['prompt_version']}`): mean **{num(judge['mean'], 2)}** of 5 (n={judge['n']}, "
        f"failures {judge['failures']}); distribution {judge['distribution']}. By coverage: "
        f"{_strata(judge['by_coverage'], 2)}. By ground-truth label: "
        f"{_strata(judge['by_ground_truth'], 2)}."
        + (f" **{judge['note']}.**" if judge.get("note") else ""),
    ]
    if quality["word_limit_exceeded"]:
        listed = ", ".join(
            f"{row['section']} {row['number']} ({row['words']}/{row['limit']})"
            for row in quality["word_limit_exceeded"]
        )
        lines.append(f"- Word limit exceeded (reported, never truncated): {listed}.")
    return lines


def _render_traceability(trace: Mapping[str, Any]) -> list[str]:
    tiers = trace["span_tiers"]
    per_answer = trace["per_answer_supported_share"]
    supported = ratio(
        trace["supported_sentences"], trace["substantive_sentences"], trace["supported_share"]
    )
    lines = ["### Traceability", ""]
    lines += [
        f"- Substantive sentences supported: **{supported}** across {trace['answers']} "
        f"answer(s); per answer mean {pct(per_answer['mean'])}, median "
        f"{pct(per_answer['median'])}, minimum {pct(per_answer['min'])}; "
        f"{per_answer['answers_fully_supported']} answer(s) fully supported.",
        f"- Support statuses over all segments: {trace['status_counts']}.",
        f"- Cited document spans: {tiers['cited_sources']}; located {tiers['located']}: "
        f"**{tiers['verbatim']} verbatim ({pct(tiers['verbatim_share_of_cited'])} of cited), "
        f"{tiers['near']} near ({pct(tiers['near_share_of_cited'])} of cited)**; not located "
        f"{tiers['not_located']}.",
        "- Human spot check (does the cited span support the sentence): "
        f"**{_spot_check(trace['spot_check'])}**.",
        "- Share of supported sentences a human disputed: "
        f"**{_disputed(trace['disputed_supported_share'])}**.",
    ]
    return lines


def _render_latency(latency: Mapping[str, Any]) -> list[str]:
    draft = latency["draft_seconds"]
    lines = ["### Latency", ""]
    lines += [
        f"- Per-question draft time: p50 **{secs(draft['p50'])}**, p95 **{secs(draft['p95'])}**, "
        f"mean {secs(draft['mean'])}, max {secs(draft['max'])} over {draft['n']} draft(s); "
        f"{secs(draft['total'])} in total.",
        f"- Time to a triaged board for the {latency['questions']}-question pack: "
        f"**{secs(latency['time_to_triaged_board_seconds'])}** (extract_questions "
        f"{secs(latency['extract_questions_seconds'])}, triage_tender "
        f"{secs(latency['triage_tender_seconds'])}).",
        f"- {latency['note']}",
    ]
    return lines


def _render_questions(configuration: Mapping[str, Any]) -> list[str]:
    metrics = configuration["metrics"]
    recall_by_key = {
        (row["section"], row["number"]): row for row in metrics["retrieval"]["per_question"]
    }
    quality_by_key = {
        (row["section"], row["number"]): row for row in metrics["draft_quality"]["per_question"]
    }
    rows = []
    for question in configuration["questions"]:
        key = (question["section"], question["number"])
        recall_row = recall_by_key.get(key, {})
        quality_row = quality_by_key.get(key, {})
        hit = recall_row.get("hit")
        seconds = question["draft_seconds"]
        rows.append(
            [
                question["section"],
                question["number"],
                question["response_type"],
                f"{question['coverage']} ({question['label_source'] or '-'}, "
                f"{num(question['best_vec'], 2)})",
                quality_row.get("ground_truth_label"),
                question["outcome"],
                question["sentences"],
                num(question["support_score"], 2),
                "yes" if hit else ("no" if hit is False else "-"),
                num(quality_row.get("edit_distance")),
                num(quality_row.get("judge_score"), 0),
                secs(seconds) if seconds is not None else "-",
            ]
        )
    lines = ["### Per question", ""]
    lines += table(
        [
            "Section", "No.", "Type", "Coverage (source, best_vec)", "Truth", "Outcome",
            "Sentences", "Support score", "Recall hit", "Edit dist.", "Judge", "Draft time",
        ],
        rows,
    )
    return lines


def _render_configuration(configuration: Mapping[str, Any]) -> list[str]:
    metrics = configuration["metrics"]
    outcomes = metrics["draft_quality"]["outcomes"]
    suffix = " (baseline)" if configuration["baseline"] else ""
    lines = [f"## Configuration `{configuration['label']}`{suffix}", ""]
    lines.append(
        f"Tender `{configuration['tender_id']}`: {configuration['question_count']} question(s) "
        f"extracted, {outcomes.get('drafted', 0)} drafted, {outcomes.get('skipped_pricing', 0)} "
        f"pricing question(s) never drafted, {outcomes.get('failed', 0)} failed."
    )
    rerank = configuration.get("rerank")
    if rerank:
        lines.append(
            f"LLM rerank: {rerank['calls']} call(s) over pools of up to {rerank['pool']} "
            f"candidates ({rerank['reordered']} changed the top eight, {rerank['failures']} "
            f"failed), model `{rerank['model']}`, prompt `{rerank['prompt_version']}`."
        )
    patched = _code(configuration["patched"]) or "nothing"
    lines.append(f"Patched for this configuration: {patched}.")
    if configuration.get("job_failures"):
        for failure in configuration["job_failures"]:
            lines.append(f"- Job failure `{failure['kind']}`: {failure['error']}")
    if configuration.get("pack_rows_missing") or configuration.get("pack_rows_unexpected"):
        lines.append(
            f"- Pack rows missing from the tender: {configuration['pack_rows_missing']}; "
            f"rows not in the ground truth: {configuration['pack_rows_unexpected']}."
        )
    lines += [""] + _render_retrieval(metrics["retrieval"])
    lines += [""] + _render_coverage(metrics["coverage"])
    lines += [""] + _render_quality(metrics["draft_quality"])
    lines += [""] + _render_traceability(metrics["traceability"])
    lines += [""] + _render_latency(metrics["latency"])
    lines += [""] + _render_questions(configuration)
    return lines


def _human_accuracy(value: Any) -> str:
    if value == NOT_LABELLED:
        return NOT_LABELLED
    return (
        f"{value['agree']}/{value['labelled']} ({pct(value['accuracy'])}); "
        f"confusion {value['confusion']}"
    )


def _spot_check(value: Any) -> str:
    if value == NOT_LABELLED:
        return NOT_LABELLED
    return (
        f"{value['span_supports_sentence']}/{value['labelled']} spans support their sentence "
        f"({pct(value['share'])})"
    )


def _disputed(value: Any) -> str:
    if value == NOT_LABELLED:
        return NOT_LABELLED
    return f"{value['disputed']}/{value['supported_sentences_labelled']} ({pct(value['share'])})"


def _best_vec(block: Mapping[str, Any]) -> str:
    parts = []
    for label, summary in block.items():
        if summary is None:
            parts.append(f"{label}: n/a")
        else:
            parts.append(
                f"{label}: min {num(summary['min'])}, median {num(summary['median'])}, "
                f"max {num(summary['max'])} (n={summary['n']})"
            )
    return "; ".join(parts) or "n/a"


def _render_labels(data: Mapping[str, Any]) -> list[str]:
    spot = data["spot_check"]
    labels = data["labels"]
    lines = ["## Human labels and the spot check", ""]
    lines += [
        f"- Spot-check sample for this run: `{spot['path']}` ({spot['size']} sentence-source "
        f"pairs from configuration `{spot['configuration']}`). Fill in `supports` (true or "
        "false), `labelled_by` and an optional `note` per row and append the rows to "
        f"`{labels['labels_dir']}/spot_checks.json` under `checks`; the next run matches them "
        "by section, number, sentence and quote.",
        f"- Coverage labels: `{labels['labels_dir']}/coverage_labels.json`, one row per pack "
        "question with the human's `coverage`; see `eval/labels/README.md` for both shapes.",
        "- Metrics that need labels print `not labelled` rather than zero until labels exist "
        "for this run's sentences and questions. The plan asks whoever runs the harness to "
        "label the twenty spot-check sentences before a run's numbers are quoted.",
    ]
    if labels.get("problems"):
        lines.append("- Label file problems: " + "; ".join(labels["problems"]))
    return lines


def render_markdown(data: Mapping[str, Any]) -> str:
    info = data["data"]
    truth = info["ground_truth"]
    banner = SYNTHETIC_BANNER.format(
        data_dir=data["run"]["data_dir"], seed=info["seed"], mode=info["mode"]
    )
    lines = [
        f"# Evaluation report {data['run']['id']} — synthetic data",
        "",
        "> " + banner,
        "",
        f"Question pack `{info['question_pack']}`: {info['question_count']} question(s); ground "
        f"truth {truth['counterpart']} with a counterpart, {truth['new']} new, "
        f"{truth['with_held_out_answer']} with a held-out answer.",
        "",
    ]
    lines += _render_run(data) + [""]
    lines += _render_ingestion(data) + [""]
    lines += _render_extraction(data) + [""]
    lines += _render_toggle_table(data) + [""]
    for configuration in data["configurations"]:
        lines += _render_configuration(configuration) + [""]
    lines += _render_labels(data) + [""]
    return "\n".join(lines)


# --- Writing --------------------------------------------------------------------------------------


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"


def write_report(
    data: dict[str, Any],
    out_dir: Path,
    run_id: str,
    spot_checks: Sequence[Mapping[str, Any]],
    *,
    spot_check_configuration: str,
) -> Report:
    """Write the three files and return the ``Report``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    spotcheck_path = out_dir / f"{run_id}.spotcheck.json"
    data["spot_check"] = {
        "path": str(spotcheck_path),
        "size": len(spot_checks),
        "configuration": spot_check_configuration,
    }
    spotcheck_path.write_text(
        _dump(
            {
                "run_id": run_id,
                "synthetic_data": True,
                "configuration": spot_check_configuration,
                "instructions": SPOT_CHECK_INSTRUCTIONS,
                "checks": list(spot_checks),
            }
        ),
        encoding="utf-8",
    )
    json_path = out_dir / f"{run_id}.json"
    json_path.write_text(_dump(data), encoding="utf-8")
    markdown_path = out_dir / f"{run_id}.md"
    markdown_path.write_text(render_markdown(data), encoding="utf-8")
    return Report(
        run_id=run_id,
        data=data,
        markdown_path=markdown_path,
        json_path=json_path,
        spotcheck_path=spotcheck_path,
    )


__all__ = [
    "SPOT_CHECK_INSTRUCTIONS",
    "SYNTHETIC_BANNER",
    "Report",
    "render_markdown",
    "write_report",
]

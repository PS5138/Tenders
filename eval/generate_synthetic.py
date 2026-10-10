"""Synthetic data generator (build order Phase 0).

Produces into an output directory (``eval/data`` by default):

- three synthetic past submissions as docx, one per layout named in ingestion step 3, each
  with 10 to 14 question-answer pairs, with deliberate overlap between them;
- two reference documents of one unkeyed kind (``iso_27001``) whose effective dates are
  stated in their first paragraph;
- one question pack as xlsx modelled on the held-out submission plus further questions;
- ``ground_truth.json`` mapping every pack question to the ingested items that answer it;
- ``manifest.json`` describing every file for the harness.

Two writers: with a real LLM provider, Claude writes the answers from a content brief; with
``LLM_PROVIDER=fake`` a deterministic template writer does, so everything runs offline.

    python -m eval.generate_synthetic --out eval/data [--seed 0] [--mode auto|template|claude]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from eval.synthetic.content import (  # noqa: E402
    COMPANY,
    CONCEPTS,
    HELD_OUT_KEY,
    PACK_QUESTION_VARIANT,
    QUESTION_PACK_CONCEPT_IDS,
    QUESTION_PACK_FILENAME,
    REFERENCE_DOCUMENTS,
    SECTION_TITLES,
    SECTIONS,
    SUBMISSIONS,
    Concept,
    SubmissionSpec,
    facts_as_at,
    format_date,
)
from eval.synthetic.documents import (  # noqa: E402
    AnswerBlock,
    PackRow,
    SubmissionSection,
    write_question_pack_xlsx,
    write_reference_docx,
    write_submission_docx,
)
from eval.synthetic.ground_truth import build_ground_truth  # noqa: E402
from eval.synthetic.prose import render_answer, word_count  # noqa: E402
from eval.synthetic.specification import (  # noqa: E402
    SPECIFICATION_FILENAME,
    write_specification_docx,
)
from eval.synthetic.specification import (  # noqa: E402
    manifest_entry as specification_manifest_entry,
)

MODE_TEMPLATE = "template"
MODE_CLAUDE = "claude"
MODES = (MODE_TEMPLATE, MODE_CLAUDE)

DEFAULT_OUT_DIR = REPO_ROOT / "eval" / "data"


def resolve_mode(mode: str | None) -> str:
    """``None`` or ``auto`` selects by ``LLM_PROVIDER``: the fake provider means templates."""
    if mode in (None, "auto"):
        from app.config import get_settings

        settings = get_settings()
        if settings.llm_provider == "fake":
            return MODE_TEMPLATE
        if not (settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")):
            raise RuntimeError(
                "LLM_PROVIDER is not 'fake' but no ANTHROPIC_API_KEY is set; pass "
                "--mode template for the offline writer or set the key for the Claude writer"
            )
        return MODE_CLAUDE
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    return mode


def submission_context(spec: SubmissionSpec) -> dict[str, str]:
    return {
        **COMPANY,
        **facts_as_at(spec.submission_date).as_dict(),
        "buyer": spec.buyer,
        "submission_date": format_date(spec.submission_date),
    }


def _flips_for(spec: SubmissionSpec, concept_id: str) -> int:
    if spec.held_out:
        return 2
    return 1 if concept_id in spec.near_identical_to else 0


def write_answers(
    spec: SubmissionSpec,
    context: dict[str, str],
    *,
    mode: str,
    seed: int,
    earlier: dict[str, dict[str, list[str]]],
) -> tuple[dict[str, list[str]], dict[str, object]]:
    """Answers keyed by concept id, plus writer metadata for the manifest."""
    concepts = [CONCEPTS[concept_id] for concept_id in spec.concept_ids]
    if mode == MODE_TEMPLATE:
        answers = {
            concept.id: render_answer(
                concept, context, seed=seed, flips=_flips_for(spec, concept.id)
            )
            for concept in concepts
        }
        return answers, {"writer": MODE_TEMPLATE, "model": None, "prompt_version": None}

    from eval.synthetic.llm_writer import write_answers_with_llm

    base_answers = {
        concept_id: earlier[source_key][concept_id]
        for concept_id, source_key in spec.near_identical_to.items()
        if concept_id in earlier.get(source_key, {})
    }
    result = write_answers_with_llm(spec, concepts, context, seed=seed, base_answers=base_answers)
    return result.answers, {
        "writer": MODE_CLAUDE,
        "model": result.model,
        "prompt_version": result.prompt_version,
        "template_fallback_concept_ids": result.fallback_concept_ids,
    }


def layout_sections(
    spec: SubmissionSpec, answers: dict[str, list[str]]
) -> tuple[list[SubmissionSection], dict[str, str]]:
    """Group the submission's concepts by pack section, numbered in the buyer's own scheme.

    Returns the sections and the question text used for each concept in this document.
    """
    by_section: dict[str, list[Concept]] = defaultdict(list)
    for concept_id in spec.concept_ids:
        concept = CONCEPTS[concept_id]
        by_section[concept.section].append(concept)

    sections: list[SubmissionSection] = []
    question_texts: dict[str, str] = {}
    section_number = 0
    for section_key in SECTIONS:
        concepts = by_section.get(section_key)
        if not concepts:
            continue
        section_number += 1
        blocks = []
        for index, concept in enumerate(concepts, start=1):
            question = concept.question_variant(spec.question_variant)
            question_texts[concept.id] = question
            blocks.append(
                AnswerBlock(
                    number=f"{section_number}.{index}",
                    section_title=SECTION_TITLES[section_key],
                    question=question,
                    paragraphs=tuple(answers[concept.id]),
                    word_limit=concept.word_limit,
                    concept_id=concept.id,
                )
            )
        sections.append(
            SubmissionSection(
                number=section_number, title=SECTION_TITLES[section_key], blocks=tuple(blocks)
            )
        )
    return sections, question_texts


def pack_rows() -> dict[str, PackRow]:
    """Pack rows keyed by concept id, numbered within each section in taxonomy order."""
    counters: dict[str, int] = defaultdict(int)
    rows: dict[str, PackRow] = {}
    for concept_id in QUESTION_PACK_CONCEPT_IDS:
        concept = CONCEPTS[concept_id]
        counters[concept.section] += 1
        section_label = SECTIONS[concept.section]
        prefix = section_label.split(".", 1)[0]
        rows[concept_id] = PackRow(
            section=section_label,
            number=f"{prefix}.{counters[concept.section]}",
            question=concept.question_variant(PACK_QUESTION_VARIANT),
            word_limit=concept.word_limit,
            weighting=concept.weighting,
            response_type=concept.response_type,
            mandatory=concept.mandatory,
        )
    return rows


def generate(out_dir: Path | str, seed: int = 0, *, mode: str | None = None) -> dict[str, object]:
    """Write every output file into ``out_dir`` and return the manifest."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    mode = resolve_mode(mode)

    submissions = {spec.key: spec for spec in SUBMISSIONS}
    all_answers: dict[str, dict[str, list[str]]] = {}
    all_question_texts: dict[str, dict[str, str]] = {}
    manifest_documents: list[dict[str, object]] = []

    for spec in SUBMISSIONS:
        context = submission_context(spec)
        answers, writer_meta = write_answers(
            spec, context, mode=mode, seed=seed, earlier=all_answers
        )
        all_answers[spec.key] = answers
        sections, question_texts = layout_sections(spec, answers)
        all_question_texts[spec.key] = question_texts
        write_submission_docx(out_path / spec.filename, spec, sections, context)
        manifest_documents.append(
            {
                "filename": spec.filename,
                "role": "held_out_submission" if spec.held_out else "past_submission",
                "key": spec.key,
                "layout": spec.layout,
                "expected": {
                    "doc_type": "past_submission",
                    "buyer": spec.buyer,
                    "submission_date": spec.submission_date.isoformat(),
                    "effective_date": spec.submission_date.isoformat(),
                    "pair_count": len(spec.concept_ids),
                },
                "pairs": [
                    {
                        "number": block.number,
                        "section": section.title,
                        "concept_id": block.concept_id,
                        "question_text": block.question,
                        "answer_word_count": word_count(list(block.paragraphs)),
                    }
                    for section in sections
                    for block in section.blocks
                ],
                "near_identical_to": [
                    {
                        "concept_id": concept_id,
                        "submission_filename": submissions[source].filename,
                    }
                    for concept_id, source in spec.near_identical_to.items()
                ],
                **writer_meta,
            }
        )

    reference_context = {**COMPANY}
    for reference in REFERENCE_DOCUMENTS:
        write_reference_docx(out_path / reference.filename, reference, reference_context)
        manifest_documents.append(
            {
                "filename": reference.filename,
                "role": "reference",
                "key": reference.key,
                "expected": {
                    "doc_type": "reference",
                    "doc_kind": reference.doc_kind,
                    "effective_date": reference.effective_date.isoformat(),
                    "effective_date_text": format_date(reference.effective_date),
                    "certificate_number": reference.certificate_number,
                },
            }
        )

    rows = pack_rows()
    write_question_pack_xlsx(out_path / QUESTION_PACK_FILENAME, list(rows.values()))
    manifest_documents.append(
        {
            "filename": QUESTION_PACK_FILENAME,
            "role": "question_pack",
            "expected": {
                "doc_type": "tender_document",
                "tender_doc_kind": "question_pack",
                "question_count": len(rows),
                "buyer": submissions[HELD_OUT_KEY].buyer,
            },
        }
    )

    write_specification_docx(out_path / SPECIFICATION_FILENAME)
    manifest_documents.append(specification_manifest_entry(submissions[HELD_OUT_KEY].buyer))

    ground_truth = build_ground_truth(
        pack_rows=rows,
        submissions=submissions,
        question_texts=all_question_texts,
        answers=all_answers,
        pack_filename=QUESTION_PACK_FILENAME,
        seed=seed,
    )
    (out_path / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    manifest: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "mode": mode,
        "supplier": COMPANY["company"],
        "note": (
            "Synthetic data. Every organisation, person, certificate and reference in these "
            "files is invented for evaluation; hackathon numbers derived from them must be "
            "labelled as synthetic."
        ),
        "documents": manifest_documents,
        "ground_truth": "ground_truth.json",
    }
    (out_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic evaluation corpus.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="output directory")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--mode",
        choices=("auto", *MODES),
        default="auto",
        help="auto picks by LLM_PROVIDER: fake selects template, anything else selects claude",
    )
    args = parser.parse_args(argv)
    manifest = generate(args.out, seed=args.seed, mode=args.mode)
    print(f"Wrote {len(manifest['documents'])} documents to {args.out} (mode={manifest['mode']})")
    for document in manifest["documents"]:  # type: ignore[union-attr]
        print(f"  {document['role']:<20} {document['filename']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

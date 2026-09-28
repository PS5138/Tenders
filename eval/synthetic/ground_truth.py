"""The held-out mapping written to ``ground_truth.json``.

Each pack question (keyed by section and number) lists the ingested items that answer it as
``{submission_filename, question_text}`` pairs, one per non-held-out submission that answered
the same concept, and carries the label ``new`` when there is none. The held-out submission's
own answer is included where it exists so draft quality can be scored without re-parsing.
"""

from __future__ import annotations

from collections.abc import Mapping

from eval.synthetic.content import (
    HELD_OUT_KEY,
    PACK_COUNTERPART_ALIASES,
    SECTIONS,
    SubmissionSpec,
)
from eval.synthetic.documents import PackRow

LABEL_COUNTERPART = "counterpart"
LABEL_NEW = "new"


def build_ground_truth(
    *,
    pack_rows: Mapping[str, PackRow],
    submissions: Mapping[str, SubmissionSpec],
    question_texts: Mapping[str, Mapping[str, str]],
    answers: Mapping[str, Mapping[str, list[str]]],
    pack_filename: str,
    seed: int,
) -> dict[str, object]:
    """``pack_rows``, ``question_texts`` and ``answers`` are keyed by concept id (and, for the
    latter two, first by submission key)."""
    held_out = submissions[HELD_OUT_KEY]
    questions: list[dict[str, object]] = []
    for concept_id, row in pack_rows.items():
        answering_concept = PACK_COUNTERPART_ALIASES.get(concept_id, concept_id)
        counterparts = [
            {
                "submission_filename": spec.filename,
                "question_text": question_texts[spec.key][answering_concept],
                "concept_id": answering_concept,
            }
            for spec in submissions.values()
            if not spec.held_out and answering_concept in spec.concept_ids
        ]
        held_out_paragraphs = answers.get(held_out.key, {}).get(concept_id)
        held_out_answer = "\n\n".join(held_out_paragraphs) if held_out_paragraphs else None
        questions.append(
            {
                "section": row.section,
                "number": row.number,
                "question_text": row.question,
                "concept_id": concept_id,
                "response_type": row.response_type,
                "label": LABEL_COUNTERPART if counterparts else LABEL_NEW,
                "counterparts": counterparts,
                "held_out_answer": held_out_answer,
            }
        )
    return {
        "seed": seed,
        "question_pack": pack_filename,
        "held_out_submission": held_out.filename,
        "ingested_submissions": [
            spec.filename for spec in submissions.values() if not spec.held_out
        ],
        "sections": list(SECTIONS.values()),
        "labels": {
            LABEL_COUNTERPART: "at least one ingested item answers the question",
            LABEL_NEW: "no ingested item answers the question",
        },
        "questions": questions,
    }

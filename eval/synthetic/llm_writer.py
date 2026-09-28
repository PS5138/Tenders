"""Claude-backed answer writer, used when a real LLM provider is configured.

One schema-validated call per past submission, through ``app.llm.get_llm().parse`` with the
versioned prompt ``synthetic_answers.v1``. The call receives a content brief built from
``content.py``; the model writes the prose. Output is checked for headings and for the fixed
fact sentences, and any question the model skipped falls back to the template writer so a
run always completes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from eval.synthetic.content import COMPANY, Concept, SubmissionSpec
from eval.synthetic.prose import fixed_sentences, render_answer

PROMPT_NAME = "synthetic_answers"  # versioned in app.llm.prompts.PROMPT_VERSIONS


def prompt_version_string() -> str:
    """Recorded in the manifest: ``app.llm.prompt_version`` for this prompt."""
    from app.llm import prompt_version

    return prompt_version(PROMPT_NAME)


class SyntheticAnswer(BaseModel):
    concept_id: str
    paragraphs: list[str] = Field(min_length=1)


class SyntheticAnswerBatch(BaseModel):
    answers: list[SyntheticAnswer]


@dataclass
class WriterResult:
    answers: dict[str, list[str]]
    model: str
    prompt_version: str
    fallback_concept_ids: list[str] = field(default_factory=list)


_HEADING_LIKE = re.compile(
    r"^\s*(#+\s|\*\*|\d+(\.\d+)*\s+[A-Z][^.]{0,60}$|[A-Z][^.!?]{0,60}:?\s*$)"
)
_LIST_MARKER = re.compile(r"^\s*([-*•]|\d+[.)])\s+")


def _clean_paragraphs(paragraphs: list[str]) -> list[str]:
    """Drop heading-like or list-marker lines; the corpus must contain no headings in answers."""
    cleaned: list[str] = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        kept = [
            _LIST_MARKER.sub("", line)
            for line in lines
            if not _HEADING_LIKE.match(line) or len(line.split()) > 12
        ]
        if kept:
            cleaned.append(" ".join(kept))
    return cleaned


def build_brief(
    spec: SubmissionSpec,
    concepts: list[Concept],
    context: Mapping[str, str],
    base_answers: Mapping[str, list[str]],
) -> dict[str, object]:
    questions = []
    for concept in concepts:
        entry: dict[str, object] = {
            "concept_id": concept.id,
            "question": concept.question_variant(spec.question_variant),
            "topic": concept.topic,
            "content_brief": concept.brief,
            "target_words": int((concept.word_limit or 400) * 0.7),
            "must_include_verbatim": fixed_sentences(concept, context),
        }
        if concept.id in base_answers:
            entry["base_answer"] = base_answers[concept.id]
        questions.append(entry)
    return {
        "supplier": {
            key: context[key]
            for key in ("company", "short", "product", "product_desc", "cso", "dpo")
        },
        "supplier_facts": {
            key: value for key, value in context.items() if key not in COMPANY and key != "buyer"
        },
        "buyer": spec.buyer,
        "tender_title": spec.tender_title,
        "submission_date": context.get("submission_date"),
        "questions": questions,
    }


def write_answers_with_llm(
    spec: SubmissionSpec,
    concepts: list[Concept],
    context: Mapping[str, str],
    *,
    seed: int,
    base_answers: Mapping[str, list[str]],
) -> WriterResult:
    from app.config import get_settings
    from app.llm import get_llm, load_prompt

    settings = get_settings()
    system = load_prompt(PROMPT_NAME)
    brief = build_brief(spec, concepts, context, base_answers)
    batch = get_llm().parse(
        PROMPT_NAME,
        model=settings.model_main,
        system=system,
        user=json.dumps(brief, ensure_ascii=False, indent=2),
        output_model=SyntheticAnswerBatch,
        max_tokens=16000,
    )
    returned = {answer.concept_id: _clean_paragraphs(answer.paragraphs) for answer in batch.answers}

    answers: dict[str, list[str]] = {}
    fallbacks: list[str] = []
    for concept in concepts:
        paragraphs = returned.get(concept.id)
        if not paragraphs:
            fallbacks.append(concept.id)
            paragraphs = render_answer(concept, context, seed=seed, flips=0)
        else:
            # A fixed fact sentence the model dropped is prepended so the fact is still stated.
            text = " ".join(paragraphs)
            for sentence in fixed_sentences(concept, context):
                if sentence not in text:
                    paragraphs[0] = f"{sentence} {paragraphs[0]}"
        answers[concept.id] = paragraphs
    return WriterResult(
        answers=answers,
        model=settings.model_main,
        prompt_version=prompt_version_string(),
        fallback_concept_ids=fallbacks,
    )

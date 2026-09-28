"""Step 2: classify a library document with one LLM call over its first sections and a sample
of later ones.

Sets ``doc_type``, ``doc_kind``, ``effective_date`` (with its source), ``buyer`` and
``submission_date`` on the document. A null extracted date falls back to the upload time with
``effective_date_source = upload_time``. The pipeline does not wait for the user's confirmation;
``classification_confirmed`` is left as it is.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import enums as e
from app.db.models import Document, DocumentSection
from app.ingest.types import ClassificationOutput
from app.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "classify_document"
_HEAD_SECTIONS = 12
_SAMPLE_SECTIONS = 8
_SECTION_CHARS = 900


def select_sample(sections: Sequence[DocumentSection]) -> list[DocumentSection]:
    """The first ``_HEAD_SECTIONS`` sections plus up to ``_SAMPLE_SECTIONS`` spread evenly over
    the rest, in document order."""
    head = list(sections[:_HEAD_SECTIONS])
    rest = list(sections[_HEAD_SECTIONS:])
    if not rest:
        return head
    if len(rest) <= _SAMPLE_SECTIONS:
        return head + rest
    step = len(rest) / _SAMPLE_SECTIONS
    picked = [rest[min(int(i * step), len(rest) - 1)] for i in range(_SAMPLE_SECTIONS)]
    return head + picked


def _render_section(section: DocumentSection) -> str:
    path = " > ".join(section.heading_path) if section.heading_path else "(no heading)"
    location = ""
    if section.cell_ref:
        location = f" row {section.cell_ref}"
    elif section.page_start is not None:
        location = f" page {section.page_start}"
    text = section.text
    if len(text) > _SECTION_CHARS:
        text = text[:_SECTION_CHARS].rstrip() + " […]"
    return f"[section {section.order_index}{location}] heading path: {path}\n{text}"


def build_user_prompt(document: Document, sections: Sequence[DocumentSection]) -> str:
    settings = get_settings()
    sample = select_sample(sections)
    rendered = "\n\n".join(_render_section(section) for section in sample)
    kinds = ", ".join(f"`{kind}`" for kind in settings.doc_kinds)
    return (
        f"Filename: {document.filename}\n"
        f"Sections in the document: {len(sections)}; shown: {len(sample)}.\n"
        f"Document kinds available for reference documents: {kinds}.\n\n"
        f"{rendered}"
    )


def _upload_date(document: Document) -> date:
    created = document.created_at
    if created is None:
        return datetime.now(UTC).date()
    return created.date()


def apply_classification(
    document: Document, output: ClassificationOutput, *, upload_date: date | None = None
) -> None:
    """Write the classification onto the document row (no LLM, no commit)."""
    settings = get_settings()
    document.doc_type = output.doc_type
    if output.doc_type == e.DocType.REFERENCE.value:
        kind = output.doc_kind
        if kind not in settings.doc_kinds:
            if kind:
                logger.warning(
                    "classification returned unknown doc_kind %r for %s; using 'other'",
                    kind,
                    document.filename,
                )
            kind = "other"
        document.doc_kind = kind
        document.buyer = None
        document.submission_date = None
    else:
        document.doc_kind = None
        document.buyer = (output.buyer or "").strip() or None
        document.submission_date = output.submission_date

    if output.effective_date is not None:
        document.effective_date = output.effective_date
        document.effective_date_source = e.EffectiveDateSource.EXTRACTED.value
    else:
        document.effective_date = upload_date or _upload_date(document)
        document.effective_date_source = e.EffectiveDateSource.UPLOAD_TIME.value


def classify_document(
    session: Session, document: Document, sections: Sequence[DocumentSection]
) -> None:
    """One LLM call; persists the proposal on the document (flushed, not committed).

    A document whose classification a user has already confirmed or overridden keeps its
    values: the call is skipped so a resumed job never overwrites a human decision.
    """
    if document.classification_confirmed and document.doc_type:
        logger.info("classification of %s already confirmed; skipping", document.filename)
        return
    settings = get_settings()
    output = get_llm().parse(
        PROMPT_NAME,
        model=settings.model_fast,
        system=load_prompt(PROMPT_NAME),
        user=build_user_prompt(document, sections),
        output_model=ClassificationOutput,
        max_tokens=1000,
    )
    apply_classification(document, output)
    session.flush()
    logger.info(
        "classified %s as %s/%s effective %s (%s)",
        document.filename,
        document.doc_type,
        document.doc_kind,
        document.effective_date,
        document.effective_date_source,
    )


__all__ = ["apply_classification", "build_user_prompt", "classify_document", "select_sample"]

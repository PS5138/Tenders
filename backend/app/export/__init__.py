"""Export: docx (submission and review modes) and xlsx.

``build_export`` is the one entry point. It reads the tender's questions in ``order_index``
with their current answers, applies the mode's rule for which text is written, and hands the
resulting rows to the format module. Nothing here calls another pipeline module: the caller
(the export endpoint) runs the fact-expiry sweep before a submission export and commits.

Modes (plan: Traceability, Export):

- ``submission``: every question in ``order_index`` with its section, number and text. A
  question at ``approved`` gets its current answer text; any other question, including
  pricing questions without an approved version, gets the single placeholder line
  ``[No approved answer]``. Before anything is written, ``ExportBlocked`` is raised while any
  question has ``needs_review`` true.
- ``review``: the current answer of every question that has one, regardless of status, with
  each sentence's sources as Word comments in docx; the same placeholder where no version
  exists. Never blocked.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import QuestionStatus
from app.db.models import Answer, Question, Tender

ExportFormat = Literal["docx", "xlsx"]
ExportMode = Literal["submission", "review"]

FORMATS: tuple[str, ...] = ("docx", "xlsx")
MODES: tuple[str, ...] = ("submission", "review")

# The single placeholder line written wherever the mode's rule yields no answer text.
PLACEHOLDER = "[No approved answer]"

MEDIA_TYPES: dict[str, str] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Human-readable status labels for the spreadsheet (British spelling, no identifiers leak).
STATUS_LABELS: dict[str, str] = {
    QuestionStatus.NOT_STARTED.value: "Not started",
    QuestionStatus.AI_DRAFT.value: "AI draft",
    QuestionStatus.WRITER_EDITED.value: "Writer edited",
    QuestionStatus.SME_VERIFIED.value: "SME verified",
    QuestionStatus.APPROVED.value: "Approved",
}


class ExportBlocked(Exception):
    """Raised by a submission export while any question has ``needs_review`` true.

    ``question_ids`` names the blocking questions so the endpoint can return them in the 409.
    """

    def __init__(self, question_ids: Sequence[uuid.UUID]) -> None:
        self.question_ids: list[uuid.UUID] = list(question_ids)
        count = len(self.question_ids)
        noun = "question needs" if count == 1 else "questions need"
        super().__init__(
            f"Export refused: {count} {noun} review. Resolve the flagged sentences before "
            "exporting for submission."
        )


@dataclass
class ExportRow:
    """One question as the format modules see it: pure data, no ORM objects."""

    question_id: uuid.UUID
    order_index: int
    section: str
    number: str
    text: str
    status: str
    response_type: str
    mandatory: bool
    needs_review: bool
    word_limit: int | None
    # None means the placeholder is written; otherwise the answer text to write.
    answer_text: str | None = None
    # Stored-shape segments of the written answer (review mode uses them for comments).
    segments: list[dict[str, Any]] = field(default_factory=list)
    word_count: int | None = None
    support_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def has_answer(self) -> bool:
        return self.answer_text is not None

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.replace("_", " ").capitalize())


def _validate(format: str, mode: str) -> None:
    if format not in FORMATS:
        raise ValueError(f"Unknown export format {format!r}; expected one of {FORMATS}.")
    if mode not in MODES:
        raise ValueError(f"Unknown export mode {mode!r}; expected one of {MODES}.")


def load_questions(session: Session, tender: Tender) -> list[Question]:
    """The tender's questions in the buyer's order."""
    stmt = (
        select(Question)
        .where(Question.tender_id == tender.id)
        .order_by(Question.order_index, Question.section, Question.number)
    )
    return list(session.scalars(stmt))


def load_current_answers(
    session: Session, questions: Sequence[Question]
) -> dict[uuid.UUID, Answer]:
    """Current answer per question id, for the questions that have one."""
    ids = [question.id for question in questions]
    if not ids:
        return {}
    stmt = select(Answer).where(Answer.question_id.in_(ids), Answer.is_current.is_(True))
    return {answer.question_id: answer for answer in session.scalars(stmt)}


def collect_rows(session: Session, tender: Tender, *, mode: str) -> list[ExportRow]:
    """Apply the mode's rule and return one row per question in ``order_index``.

    Submission mode raises ``ExportBlocked`` before any row is built when a question has
    ``needs_review`` true; the endpoint has already run the expiry sweep by then.
    """
    _validate("docx", mode)
    questions = load_questions(session, tender)

    if mode == "submission":
        blocked = [question.id for question in questions if question.needs_review]
        if blocked:
            raise ExportBlocked(blocked)

    answers = load_current_answers(session, questions)
    rows: list[ExportRow] = []
    for question in questions:
        answer = answers.get(question.id)
        row = ExportRow(
            question_id=question.id,
            order_index=question.order_index,
            section=question.section,
            number=question.number,
            text=question.text,
            status=question.status,
            response_type=question.response_type,
            mandatory=question.mandatory,
            needs_review=question.needs_review,
            word_limit=question.word_limit,
        )
        if answer is not None and _writes_answer(mode, question):
            row.answer_text = answer.text
            row.segments = list(answer.segments or [])
            row.word_count = answer.word_count
            row.support_summary = dict(answer.support_summary or {})
        rows.append(row)
    return rows


def _writes_answer(mode: str, question: Question) -> bool:
    if mode == "submission":
        return question.status == QuestionStatus.APPROVED.value
    return True


def build_export(session: Session, tender: Tender, *, format: str, mode: str) -> bytes:
    """Build the export file for ``tender`` and return its bytes.

    ``format`` is ``docx`` or ``xlsx``; ``mode`` is ``submission`` or ``review``. Raises
    ``ExportBlocked(question_ids)`` in submission mode while any question has ``needs_review``
    true, and ``ValueError`` for an unknown format or mode. Does not commit.
    """
    _validate(format, mode)
    rows = collect_rows(session, tender, mode=mode)
    if format == "docx":
        from app.export.docx import build_docx

        return build_docx(tender, rows, mode=mode)
    from app.export.xlsx import build_xlsx

    return build_xlsx(tender, rows, mode=mode)


def export_filename(tender: Tender, *, format: str, mode: str) -> str:
    """A safe download filename: ``<tender name>-<mode>.<format>``."""
    _validate(format, mode)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", tender.name).strip("-") or "tender"
    return f"{stem[:80]}-{mode}.{format}"


def split_paragraphs(text: str) -> list[str]:
    """Split stored answer text into paragraphs on blank lines, dropping empty ones."""
    parts = [part.strip() for part in re.split(r"\n\s*\n", text)]
    return [part for part in parts if part]


__all__ = [
    "FORMATS",
    "MEDIA_TYPES",
    "MODES",
    "PLACEHOLDER",
    "STATUS_LABELS",
    "ExportBlocked",
    "ExportFormat",
    "ExportMode",
    "ExportRow",
    "build_export",
    "collect_rows",
    "export_filename",
    "load_current_answers",
    "load_questions",
    "split_paragraphs",
]

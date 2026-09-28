"""Sentence-level review actions: attest, dispute and gap acknowledgement.

Attest and dispute modify the addressed segment of the current answer version in place; they
never create a version and are refused on a non-current version. ``support_summary`` and
``needs_review`` are recomputed by ``review.support.recompute_support``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import EventType, SourceType, SupportStatus
from app.db.models import Answer, Question, utcnow
from app.review.events import EntityType, record_event
from app.review.support import recompute_support
from app.review.transitions import current_answer, gap_is_acknowledged

ATTESTABLE: frozenset[str] = frozenset(
    {
        SupportStatus.UNSUPPORTED.value,
        SupportStatus.WEAK.value,
        SupportStatus.HUMAN_AUTHORED.value,
    }
)


class ActionNotPermitted(Exception):
    """The action is not allowed on this segment or version (HTTP 409)."""


class SegmentNotFound(LookupError):
    """No segment with that index on the answer (HTTP 404)."""


class GapNotFound(ValueError):
    """The string matches no gap in the current version after normalisation (HTTP 422)."""


def _segments(answer: Answer) -> list[dict[str, Any]]:
    return [dict(segment) for segment in (answer.segments or [])]


def _find(segments: list[dict[str, Any]], index: int) -> int:
    for position, segment in enumerate(segments):
        if int(segment.get("index", position)) == index:
            return position
    raise SegmentNotFound(f"segment {index} not found")


def _require_current(answer: Answer) -> None:
    if not answer.is_current:
        raise ActionNotPermitted("Only segments of the current answer version can be reviewed.")


def _question(session: Session, answer: Answer) -> Question:
    question = session.get(Question, answer.question_id)
    if question is None:
        raise LookupError("answer has no question")
    return question


def attest(
    session: Session, answer: Answer, index: int, actor: str, note: str | None = None
) -> Answer:
    """Add a ``human_attestation`` source to an ``unsupported``, ``weak`` or ``human_authored``
    segment of the current version; it becomes ``supported`` and any dispute is cleared."""
    _require_current(answer)
    segments = _segments(answer)
    position = _find(segments, index)
    segment = segments[position]
    if segment.get("support_status") not in ATTESTABLE:
        raise ActionNotPermitted(
            "Only unsupported, weak or human-authored sentences can be attested."
        )
    at = utcnow().isoformat()
    attestation = {
        "source_type": SourceType.HUMAN_ATTESTATION.value,
        "attested_by": actor,
        "note": note,
        "at": at,
    }
    segment["sources"] = [*[dict(s) for s in segment.get("sources", []) or []], attestation]
    segment["support_status"] = SupportStatus.SUPPORTED.value
    segment["dispute"] = None
    segments[position] = segment
    answer.segments = segments
    recompute_support(session, answer)
    question = _question(session, answer)
    record_event(
        session,
        EntityType.QUESTION,
        question.id,
        EventType.ATTESTED,
        actor,
        {"answer_id": str(answer.id), "index": index, "note": note, "at": at},
        org_id=answer.org_id,
    )
    return answer


def dispute(session: Session, answer: Answer, index: int, actor: str, note: str) -> Answer:
    """Mark a ``supported`` segment of the current version disputed: it becomes ``unsupported``,
    keeps its sources for context, carries the dispute, and the question needs review."""
    _require_current(answer)
    if not note or not note.strip():
        raise ValueError("a dispute needs a note")
    segments = _segments(answer)
    position = _find(segments, index)
    segment = segments[position]
    if segment.get("support_status") != SupportStatus.SUPPORTED.value:
        raise ActionNotPermitted("Only supported sentences can be disputed.")
    at = utcnow().isoformat()
    segment["dispute"] = {"disputed_by": actor, "note": note.strip(), "at": at}
    segment["support_status"] = SupportStatus.UNSUPPORTED.value
    segments[position] = segment
    answer.segments = segments
    question = _question(session, answer)
    question.needs_review = True
    recompute_support(session, answer)
    record_event(
        session,
        EntityType.QUESTION,
        question.id,
        EventType.DISPUTED,
        actor,
        {"answer_id": str(answer.id), "index": index, "note": note.strip(), "at": at},
        org_id=answer.org_id,
    )
    return answer


def acknowledge_gap(
    session: Session, question: Question, gap: str, note: str | None, actor: str
) -> Question:
    """Record that a person has acknowledged a gap of the current version. ``gap`` must equal
    a string in the current answer's ``gaps`` after normalisation, else ``GapNotFound``."""
    answer = current_answer(session, question)
    if answer is None or not gap_is_acknowledged(gap, [{"gap": g} for g in (answer.gaps or [])]):
        raise GapNotFound("The gap does not match any gap in the current answer.")
    at = utcnow().isoformat()
    acknowledgement = {"gap": gap, "acknowledged_by": actor, "note": note, "at": at}
    question.gap_acknowledgements = [
        *[dict(ack) for ack in (question.gap_acknowledgements or [])],
        acknowledgement,
    ]
    record_event(
        session,
        EntityType.QUESTION,
        question.id,
        EventType.GAP_ACKNOWLEDGED,
        actor,
        {"answer_id": str(answer.id), "gap": gap, "note": note, "at": at},
        org_id=question.org_id,
    )
    session.flush()
    return question

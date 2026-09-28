"""Question status transitions.

The only place that may write ``questions.status`` is ``transition()`` below. The gates
(``sme_verified`` needs every substantive segment supported and ``needs_review`` false;
``approved`` additionally needs every gap acknowledged) and the blocker shapes are specified
in the plan's review pipeline section.

Blocker shape: ``{kind: "segment" | "gap" | "needs_review" | "no_answer" | "system_only",
index?, gap?}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import EventType, QuestionStatus, SegmentKind, SupportStatus
from app.db.models import Answer
from app.ingest.normalise import normalised_equal
from app.review.events import EntityType, record_event

if TYPE_CHECKING:
    from app.db.models import Question

# The plan calls this the STATUS enum; it is defined once in app.db.enums.
Status = QuestionStatus

# Ordinal positions, so "past ai_draft" and "backwards" are comparisons.
ORDER: dict[str, int] = {status.value: index for index, status in enumerate(Status)}

# Targets a person may request through PATCH /questions/{id}. The others are system-only.
HUMAN_TARGETS: frozenset[str] = frozenset(
    {Status.WRITER_EDITED.value, Status.SME_VERIFIED.value, Status.APPROVED.value}
)
SYSTEM_ONLY_TARGETS: frozenset[str] = frozenset(
    {Status.NOT_STARTED.value, Status.AI_DRAFT.value}
)


class TransitionBlocked(Exception):
    """Raised when a transition is not allowed; ``blockers`` has the API shape."""

    def __init__(self, to: str, blockers: list[dict[str, Any]]) -> None:
        super().__init__(f"transition to {to} blocked")
        self.to = to
        self.blockers = blockers


def current_answer(session: Session, question: Question) -> Answer | None:
    """The question's current answer version, or None. Flushes pending state first so a
    version made current in this transaction is seen."""
    session.flush()
    return session.scalars(
        select(Answer).where(Answer.question_id == question.id, Answer.is_current.is_(True))
    ).first()


def is_past_ai_draft(status: str) -> bool:
    """Whether ``status`` is one a person has stood behind (writer_edited or later)."""
    return ORDER[str(status)] > ORDER[Status.AI_DRAFT.value]


def segment_blockers(answer: Answer) -> list[dict[str, Any]]:
    """Indices of substantive segments that are not ``supported`` (by a document source or a
    human attestation), including un-attested ``human_authored`` ones."""
    return [
        {"kind": "segment", "index": int(segment.get("index", position))}
        for position, segment in enumerate(answer.segments or [])
        if segment.get("kind", SegmentKind.SUBSTANTIVE.value) == SegmentKind.SUBSTANTIVE.value
        and segment.get("support_status") != SupportStatus.SUPPORTED.value
    ]


def gap_is_acknowledged(gap: str, acknowledgements: list[dict[str, Any]]) -> bool:
    """An acknowledgement matches a gap when the two strings are equal after normalisation."""
    return any(normalised_equal(gap, str(ack.get("gap", ""))) for ack in acknowledgements)


def unacknowledged_gaps(question: Question, answer: Answer) -> list[str]:
    """Gaps of the current version that no acknowledgement on the question matches."""
    acknowledgements = list(question.gap_acknowledgements or [])
    return [
        gap for gap in (answer.gaps or []) if not gap_is_acknowledged(str(gap), acknowledgements)
    ]


def gap_blockers(question: Question, answer: Answer) -> list[dict[str, Any]]:
    return [{"kind": "gap", "gap": gap} for gap in unacknowledged_gaps(question, answer)]


def blockers_for(
    question: Question, answer: Answer | None, to: str, *, system: bool = False
) -> list[dict[str, Any]]:
    """The blockers a move to ``to`` would meet. Empty means allowed."""
    target = Status(to).value
    if target in SYSTEM_ONLY_TARGETS and not system:
        return [{"kind": "system_only"}]
    if answer is None:
        # Reached only from not_started: no version exists.
        return [{"kind": "no_answer"}]
    if target in (Status.NOT_STARTED.value, Status.AI_DRAFT.value, Status.WRITER_EDITED.value):
        return []
    blockers = segment_blockers(answer)
    if question.needs_review:
        blockers.append({"kind": "needs_review"})
    if target == Status.APPROVED.value:
        blockers.extend(gap_blockers(question, answer))
    return blockers


def allowed_transitions(session: Session, question: Question) -> list[dict[str, Any]]:
    """``[{to, allowed, blockers}]`` for every status, per the plan's transition matrix,
    from the point of view of a person using PATCH /questions/{id}."""
    answer = current_answer(session, question)
    result: list[dict[str, Any]] = []
    for status in Status:
        blockers = blockers_for(question, answer, status.value)
        result.append({"to": status.value, "allowed": not blockers, "blockers": blockers})
    return result


def transition(
    session: Session,
    question: Question,
    to: str | Status,
    actor: str,
    *,
    system: bool = False,
) -> Question:
    """Move ``question`` to ``to``, writing a ``status_changed`` event; a request for the
    current status is a no-op that writes nothing. Raises ``TransitionBlocked`` with the
    blockers otherwise.

    ``system=True`` is for pipeline code (``set_current_ai_version`` moving to ``ai_draft``);
    a request from a person never passes it, so ``not_started`` and ``ai_draft`` stay
    system-only for PATCH. This is the only writer of ``questions.status``.
    """
    target = Status(to).value
    # The same-status short-circuit comes first so echoing the current status is a no-op for
    # every status, including the system-only ones: a full-row PATCH that repeats
    # ``ai_draft`` or ``not_started`` must not be refused and drop its other fields.
    if question.status == target:
        return question
    if target in SYSTEM_ONLY_TARGETS and not system:
        raise TransitionBlocked(target, [{"kind": "system_only"}])
    answer = current_answer(session, question)
    blockers = blockers_for(question, answer, target, system=system)
    if blockers:
        raise TransitionBlocked(target, blockers)
    previous = question.status
    question.status = target
    record_event(
        session,
        EntityType.QUESTION,
        question.id,
        EventType.STATUS_CHANGED,
        actor,
        {
            "from": previous,
            "to": target,
            "answer_id": str(answer.id) if answer is not None else None,
        },
        org_id=question.org_id,
    )
    return question

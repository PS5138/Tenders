"""Save an assistant message as an answer version (plan: Chat threads).

Creates a new ``ai`` version on the thread's question, copies the message's segments, gaps,
fact checklist, verbatim offer, model and prompt version, derives ``text`` and ``word_count``
from the segments, links the message to the version, and makes it current under the
displacement rule (status moves to ``ai_draft`` through the transition function).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import AuthorType, EventType, MessageRole, ResponseType, SupportStatus
from app.db.models import Answer, Message, Question, Thread
from app.generate.segmentation import join_segments
from app.review.events import EntityType, record_event
from app.review.versions import check_displacement, next_version, set_current_ai_version


class MessageNotSaveable(Exception):
    """The message cannot become an answer: it is not an assistant message with segments, its
    thread is tender-level and has no question, or its question is a pricing question, which
    is never drafted (HTTP 409)."""


def _stored_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of the message's segments; ``pending`` is a wire status and is never stored,
    so a stray one is recorded as ``unsupported``."""
    copied: list[dict[str, Any]] = []
    for position, stored in enumerate(segments):
        segment = dict(stored)
        segment.setdefault("index", position)
        segment["sources"] = [dict(source) for source in (segment.get("sources") or [])]
        segment["dispute"] = dict(segment["dispute"]) if segment.get("dispute") else None
        if segment.get("support_status") not in {status.value for status in SupportStatus}:
            segment["support_status"] = SupportStatus.UNSUPPORTED.value
        copied.append(segment)
    return copied


def save_message_as_answer(
    session: Session, message: Message, actor: str, confirm_displace: bool = False
) -> Answer:
    """Create a current ``ai`` version from an assistant message. Raises
    ``MessageNotSaveable`` for a user message, a message without segments or a tender-level
    thread, and ``DisplacementRequired`` (before writing) when the question is past
    ``ai_draft`` and ``confirm_displace`` is false. The caller commits."""
    if message.role != MessageRole.ASSISTANT.value or message.segments is None:
        raise MessageNotSaveable("Only assistant messages with segments can be saved as answers.")
    thread = session.get(Thread, message.thread_id)
    if thread is None or thread.question_id is None:
        raise MessageNotSaveable(
            "This message belongs to a tender-level thread and has no question to answer."
        )
    question = session.get(Question, thread.question_id)
    if question is None:
        raise MessageNotSaveable("The thread's question no longer exists.")
    if question.response_type == ResponseType.PRICING.value:
        # Plan, review pipeline: pricing questions receive no AI draft; their answer region
        # is a human-only editor. Refused before check_displacement so nothing is written.
        raise MessageNotSaveable("Pricing questions are never drafted.")
    check_displacement(session, question, confirm_displace)

    segments = _stored_segments(list(message.segments))
    text = join_segments(segments)
    answer = Answer(
        org_id=question.org_id,
        question_id=question.id,
        version=next_version(session, question),
        author_type=AuthorType.AI.value,
        author_name=None,
        text=text,
        word_count=len(text.split()),
        segments=segments,
        gaps=list(message.gaps or []),
        fact_checklist=[dict(entry) for entry in (message.fact_checklist or [])],
        verbatim_offer_item_id=message.verbatim_offer_item_id,
        verbatim_source_item_id=None,
        model=message.model,
        prompt_version=message.prompt_version,
        is_current=False,
    )
    set_current_ai_version(session, question, answer, actor, confirm_displace)
    message.answer_id = answer.id
    record_event(
        session,
        EntityType.QUESTION,
        question.id,
        EventType.ANSWER_SAVED_FROM_CHAT,
        actor,
        {
            "answer_id": str(answer.id),
            "version": answer.version,
            "message_id": str(message.id),
            "thread_id": str(thread.id),
        },
        org_id=question.org_id,
    )
    session.flush()
    return answer

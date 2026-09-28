"""Answer versions: the displacement rule, making a version current, and the human edit.

``set_current_ai_version`` is how every AI-authored version (card draft, save-as-answer,
accept-verbatim, draft-all) becomes current: it enforces the displacement rule, keeps one
current version per question, recomputes support and moves status to ``ai_draft`` through
the transition function.

``save_human_edit`` implements the plan's editing behaviour exactly: re-split with the
authoritative splitter, re-align against the base version's stored sentences with
``difflib.SequenceMatcher``, drop attestations and disputes on changed sentences, re-verify
sentences that became ``weak`` by edit, copy gaps and the fact checklist, and land at
``writer_edited`` whatever the status was before.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.enums import AuthorType, EventType, SegmentKind, SourceType, SupportStatus
from app.db.models import Answer, Question
from app.generate.segmentation import join_segments, split_sentences
from app.ingest.normalise import normalise
from app.review.events import EntityType, record_event
from app.review.support import recompute_support
from app.review.transitions import Status, current_answer, is_past_ai_draft, transition

logger = logging.getLogger(__name__)

_DOCUMENT_SOURCE_TYPES = frozenset({SourceType.KNOWLEDGE_ITEM.value, SourceType.FACT.value})


class DisplacementRequired(Exception):
    """An AI write would displace a version a person has stood behind; the request must carry
    ``confirm_displace: true``."""

    def __init__(self, question: Question, current: Answer | None) -> None:
        super().__init__(
            f"question is at {question.status}; confirm_displace is required to replace the "
            "current version with an AI version"
        )
        self.question = question
        self.current_answer = current


class StaleBaseVersion(Exception):
    """``base_version_id`` is not the current version; nothing was written."""

    def __init__(self, current: Answer | None) -> None:
        super().__init__("base_version_id is not the current version")
        self.current_answer = current


class EmptyEdit(Exception):
    """The saved text yields no sentences under the authoritative splitter (whitespace only);
    nothing was written. The API maps it to 422. ``detail`` is the user-facing message."""

    detail = "The answer text contains no sentences."

    def __init__(self) -> None:
        super().__init__(self.detail)


def next_version(session: Session, question: Question) -> int:
    session.flush()
    highest = session.scalar(
        select(func.max(Answer.version)).where(Answer.question_id == question.id)
    )
    return int(highest or 0) + 1


def check_displacement(session: Session, question: Question, confirm_displace: bool) -> None:
    """Raise ``DisplacementRequired`` when the status is past ``ai_draft`` and the caller has
    not confirmed. Call it before creating anything so a refusal writes nothing."""
    if is_past_ai_draft(question.status) and not confirm_displace:
        raise DisplacementRequired(question, current_answer(session, question))


def make_current(session: Session, question: Question, answer: Answer) -> None:
    """Make ``answer`` the one current version of ``question``. The displaced version stays in
    the history with ``is_current = false``."""
    if answer.question_id is None:
        answer.question_id = question.id
    if answer.question_id != question.id:
        raise ValueError("answer belongs to a different question")
    if answer.org_id is None:
        answer.org_id = question.org_id
    if answer.version is None:
        answer.version = next_version(session, question)
    session.add(answer)
    session.flush()
    others = session.scalars(
        select(Answer).where(
            Answer.question_id == question.id, Answer.is_current.is_(True), Answer.id != answer.id
        )
    ).all()
    for other in others:
        other.is_current = False
    session.flush()  # release the partial unique index before the new version takes it
    answer.is_current = True
    session.flush()


def set_current_ai_version(
    session: Session,
    question: Question,
    answer: Answer,
    actor: str,
    confirm_displace: bool = False,
) -> None:
    """Make an AI-authored ``answer`` current under the displacement rule, recompute support
    and move the question to ``ai_draft`` through the transition function.

    Raises ``DisplacementRequired`` (before writing anything) when the status is past
    ``ai_draft`` and ``confirm_displace`` is false. The caller commits.

    The question row is re-read under ``SELECT ... FOR UPDATE`` first: the caller may have
    loaded it seconds earlier (a card draft before its synthesis call, a thread message before
    save-as-answer) and a human edit committed meanwhile must not be displaced on the strength
    of a stale ``status``. The lock is held until the caller commits, so a concurrent writer
    waits rather than interleaving with the displacement check and ``make_current``.
    """
    session.refresh(question, with_for_update=True)
    check_displacement(session, question, confirm_displace)
    if answer.author_type is None:
        answer.author_type = AuthorType.AI.value
    make_current(session, question, answer)
    recompute_support(session, answer)
    transition(session, question, Status.AI_DRAFT, actor, system=True)


# --- Human edit -------------------------------------------------------------------------------


def _normalised(text: str) -> str:
    return normalise(text)[0]


def _document_sources(segment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(source)
        for source in segment.get("sources", []) or []
        if source.get("source_type") in _DOCUMENT_SOURCE_TYPES
    ]


def _paired_segment(old: dict[str, Any], text: str, paragraph: int) -> dict[str, Any]:
    """A changed sentence paired with an old one: keeps document and fact sources, drops any
    attestation and dispute, becomes ``weak`` (or ``human_authored`` with no source left)."""
    sources = _document_sources(old)
    if not sources:
        # Nothing to re-verify against: a connective stays connective, anything else was
        # written or rewritten by a person.
        if old.get("kind") == SegmentKind.CONNECTIVE.value:
            return _new_segment(text, paragraph, SegmentKind.CONNECTIVE.value,
                                SupportStatus.CONNECTIVE.value)
        return _human_segment(text, paragraph)
    return {
        "index": -1,
        "paragraph": paragraph,
        "text": text,
        "kind": SegmentKind.SUBSTANTIVE.value,
        "sources": sources,
        "dispute": None,
        "support_status": SupportStatus.WEAK.value,
    }


def _new_segment(text: str, paragraph: int, kind: str, support_status: str) -> dict[str, Any]:
    return {
        "index": -1,
        "paragraph": paragraph,
        "text": text,
        "kind": kind,
        "sources": [],
        "dispute": None,
        "support_status": support_status,
    }


def _human_segment(text: str, paragraph: int) -> dict[str, Any]:
    return _new_segment(
        text, paragraph, SegmentKind.SUBSTANTIVE.value, SupportStatus.HUMAN_AUTHORED.value
    )


def _kept_segment(old: dict[str, Any], text: str, paragraph: int) -> dict[str, Any]:
    """An unchanged sentence keeps its segment verbatim (sources, status, dispute)."""
    kept = dict(old)
    kept["text"] = text
    kept["paragraph"] = paragraph
    kept["sources"] = [dict(source) for source in old.get("sources", []) or []]
    kept["dispute"] = dict(old["dispute"]) if old.get("dispute") else None
    return kept


def _pair_replaced_block(
    old_norm: list[str], new_norm: list[str], realign_min: float
) -> dict[int, int]:
    """Pair new sentences to old ones inside a replaced block by string ratio, highest pair
    first, each sentence used once, accepting pairs at or above ``realign_min``.
    Returns ``{new_position: old_position}``."""
    scored: list[tuple[float, int, int]] = []
    for j, new in enumerate(new_norm):
        for i, old in enumerate(old_norm):
            ratio = SequenceMatcher(None, old, new, autojunk=False).ratio()
            if ratio >= realign_min:
                scored.append((ratio, j, i))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_old: set[int] = set()
    pairs: dict[int, int] = {}
    for _, j, i in scored:
        if j in pairs or i in used_old:
            continue
        pairs[j] = i
        used_old.add(i)
    return pairs


def realign_segments(
    base_segments: list[dict[str, Any]], text: str
) -> tuple[list[dict[str, Any]], list[int]]:
    """Re-split ``text`` and re-align it against the base version's stored segments.

    Returns the new segment list (indices assigned in order) and the indices of segments that
    became ``weak`` by edit and must be re-verified.
    """
    realign_min = get_settings().realign_min
    old_segments = list(base_segments)
    old_norm = [_normalised(str(segment.get("text", ""))) for segment in old_segments]
    sentences = split_sentences(text)
    new_norm = [_normalised(sentence.text) for sentence in sentences]

    result: list[dict[str, Any]] = []
    weak_by_edit: list[int] = []

    def append(segment: dict[str, Any]) -> None:
        segment["index"] = len(result)
        result.append(segment)

    matcher = SequenceMatcher(None, old_norm, new_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                sentence = sentences[j1 + offset]
                append(_kept_segment(old_segments[i1 + offset], sentence.text,
                                     sentence.paragraph))
        elif tag == "delete":
            continue  # unpaired old sentences are dropped
        elif tag == "insert":
            for j in range(j1, j2):
                append(_human_segment(sentences[j].text, sentences[j].paragraph))
        else:  # replace
            pairs = _pair_replaced_block(old_norm[i1:i2], new_norm[j1:j2], realign_min)
            for j in range(j1, j2):
                sentence = sentences[j]
                old_position = pairs.get(j - j1)
                if old_position is None:
                    append(_human_segment(sentence.text, sentence.paragraph))
                    continue
                paired = _paired_segment(
                    old_segments[i1 + old_position], sentence.text, sentence.paragraph
                )
                append(paired)
                if paired["support_status"] == SupportStatus.WEAK.value:
                    weak_by_edit.append(paired["index"])
    return result, weak_by_edit


Verifier = Callable[[Session, list[dict[str, Any]]], list[dict[str, Any]]]


def _load_verifier() -> Verifier | None:
    """Owner E's ``verify_segments`` (draft step 9), imported lazily so this module imports
    cleanly before it exists."""
    try:
        from app.generate.verification import verify_segments
    except ImportError:
        return None
    return verify_segments


def reverify_weak_by_edit(
    session: Session, segments: list[dict[str, Any]], indices: list[int]
) -> list[dict[str, Any]]:
    """One batched entailment pass over the sentences that became ``weak`` by edit, restoring
    ``supported`` where it passes. No call is made when the band is empty; if verification is
    unavailable the sentences stay ``weak``."""
    if not indices:
        return segments
    verifier = _load_verifier()
    if verifier is None:
        logger.warning(
            "app.generate.verification is unavailable; %d edited sentence(s) stay weak",
            len(indices),
        )
        return segments
    by_index = {segment["index"]: segment for segment in segments}
    candidates = [dict(by_index[index]) for index in indices]
    verified = verifier(session, candidates)
    for outcome in verified or []:
        index = outcome.get("index")
        if index not in by_index:
            continue
        if outcome.get("support_status") == SupportStatus.SUPPORTED.value:
            target = by_index[index]
            target["support_status"] = SupportStatus.SUPPORTED.value
            target["sources"] = [dict(source) for source in outcome.get("sources", [])]
    return segments


def save_human_edit(
    session: Session,
    question: Question,
    text: str,
    base_version_id: uuid.UUID | str | None,
    actor: str,
) -> Answer:
    """Save a person's edit as a new version (plan: Editing behaviour).

    ``base_version_id`` must be the current version, else ``StaleBaseVersion`` with the
    current answer attached and nothing written. Text the splitter reduces to no sentences
    raises ``EmptyEdit`` with nothing written. When the question has no version yet (a
    pricing question's human-only editor), ``base_version_id`` is None and every sentence is
    ``human_authored``. The new version is current, ``author_type = user``, ``author_name`` is
    the actor, ``model`` and ``prompt_version`` are null, gaps and fact checklist are copied
    from the base, support is recomputed and the question moves to ``writer_edited``.

    The question row is locked (``SELECT ... FOR UPDATE``) before ``base_version_id`` is
    compared with the current version, so two people saving against the same base serialise:
    the second waits for the first to commit and then sees the new current version, getting
    ``StaleBaseVersion`` instead of a second version built on a displaced base.
    """
    session.refresh(question, with_for_update=True)
    current = current_answer(session, question)
    if current is None:
        if base_version_id is not None:
            raise StaleBaseVersion(None)
        base_segments: list[dict[str, Any]] = []
        gaps: list[str] = []
        fact_checklist: list[dict[str, Any]] = []
    else:
        if base_version_id is None or str(base_version_id) != str(current.id):
            raise StaleBaseVersion(current)
        base_segments = list(current.segments or [])
        gaps = list(current.gaps or [])
        fact_checklist = [dict(entry) for entry in (current.fact_checklist or [])]

    # An edit the authoritative splitter reduces to no sentences would persist an empty
    # version with no substantive segments, which every gate then lets through. Refuse it
    # before anything is written; the splitter, not ``str.strip``, decides (one-splitter rule).
    if not split_sentences(text):
        raise EmptyEdit()

    segments, weak_by_edit = realign_segments(base_segments, text)
    segments = reverify_weak_by_edit(session, segments, weak_by_edit)
    derived_text = join_segments(segments)

    answer = Answer(
        org_id=question.org_id,
        question_id=question.id,
        version=next_version(session, question),
        author_type=AuthorType.USER.value,
        author_name=actor,
        text=derived_text,
        word_count=len(derived_text.split()),
        segments=segments,
        gaps=gaps,
        fact_checklist=fact_checklist,
        verbatim_offer_item_id=None,
        verbatim_source_item_id=None,
        model=None,
        prompt_version=None,
        is_current=False,
    )
    make_current(session, question, answer)
    recompute_support(session, answer)
    record_event(
        session,
        EntityType.QUESTION,
        question.id,
        EventType.ANSWER_CREATED,
        actor,
        {
            "answer_id": str(answer.id),
            "version": answer.version,
            "author_type": AuthorType.USER.value,
            "base_version_id": str(current.id) if current is not None else None,
            "reverified": len(weak_by_edit),
        },
        org_id=question.org_id,
    )
    transition(session, question, Status.WRITER_EDITED, actor, system=True)
    return answer

"""The single-question draft pipeline (plan: "Draft pipeline (single question)", steps 1 to 10).

``draft_question`` runs a card draft and returns the persisted ``Answer``; ``reply_in_thread``
persists the user's message, runs the same pipeline with the thread's history and returns the
persisted assistant ``Message``. Both are synchronous, take the caller's session, flush and
never commit (the runner or job handler commits, then emits ``done``). Stream events are
pushed through an ``emit`` callback in the plan's order: ``verbatim`` (when step 6 fires),
``segment`` (conformed on the stream, ``support_status`` pending, offsets null), ``gaps``,
``fact_checklist``, ``support`` (one per segment, verified sources), and the caller adds
``done``. On any failure the caller rolls back and emits ``error``; nothing is persisted.

Cross-module calls (retrieval, facts, the review owner's version and support functions) go
through the small indirection functions below, imported lazily so this module imports cleanly
before the others land and so tests can substitute fakes.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import AnswerRecord, ThreadMessage
from app.config import get_settings
from app.db.enums import (
    WIRE_PENDING,
    AuthorType,
    EntityType,
    EventType,
    ItemType,
    MessageRole,
    QuestionStatus,
    ResponseType,
    SourceType,
)
from app.db.models import (
    Answer,
    Document,
    DocumentSection,
    Fact,
    KnowledgeItem,
    Message,
    Question,
    Tender,
    Thread,
)
from app.generate.errors import Conflict409, DraftError
from app.generate.segmentation import Conformer, join_segments
from app.generate.verbatim import build_verbatim_segments, verbatim_offer
from app.generate.verification import source_record, verify_fact_checklist, verify_segments
from app.llm import get_llm, load_prompt, prompt_version
from app.llm.client import History
from app.llm.embeddings import cosine_similarity, embed
from app.review.events import record_event
from app.review.support import summarise_support

logger = logging.getLogger(__name__)

SYNTHESIS_PROMPT = "synthesis"
QUERY_REWRITE_PROMPT = "query_rewrite"

Emit = Callable[[dict[str, Any]], None]

# Statuses past ai_draft: an AI write onto one of these needs confirm_displace.
_PAST_AI_DRAFT: frozenset[str] = frozenset(
    {
        QuestionStatus.WRITER_EDITED.value,
        QuestionStatus.SME_VERIFIED.value,
        QuestionStatus.APPROVED.value,
    }
)


def _no_emit(_: dict[str, Any]) -> None:
    return None


# --- Schemas of the model's streamed lines --------------------------------------------------------


class ModelSource(BaseModel):
    source_type: Literal["knowledge_item", "fact"]
    source_id: str
    quote: str = ""


class SegmentLine(BaseModel):
    type: Literal["segment"]
    text: str
    paragraph: int = 0
    kind: Literal["substantive", "connective"] = "substantive"
    sources: list[ModelSource] = Field(default_factory=list)


class GapsLine(BaseModel):
    type: Literal["gaps"]
    gaps: list[str] = Field(default_factory=list)


class FactChecklistLine(BaseModel):
    type: Literal["fact_checklist"]
    fact_ids: list[str] = Field(default_factory=list)


SynthesisLine = TypeAdapter(
    Annotated[SegmentLine | GapsLine | FactChecklistLine, Field(discriminator="type")]
)


class QueryRewrite(BaseModel):
    query: str


# --- Cross-module indirection (lazy imports; tests monkeypatch these names) -----------------------


def query_vector_for(
    session: Session, *, question: Question | None = None, text: str | None = None
) -> list[float]:
    """Owner C's ``get_query_vector``; until it lands, the stored question embedding or a
    call-time embedding of ``text``."""
    try:
        from app.retrieve.query import get_query_vector
    except ImportError:
        if text is None and question is not None and question.embedding is not None:
            return [float(v) for v in question.embedding]
        source = text if text is not None else (question.text if question is not None else "")
        return embed([source])[0]
    return get_query_vector(session, question=question, text=text)


def retrieve_candidates(
    session: Session,
    org_id: uuid.UUID,
    *,
    query_text: str,
    query_vector: list[float],
    topics: list[str],
) -> Any:
    """Owner C's ``retrieve``: ``.candidates`` (top eight after fusion and collapse, each with
    ``.item`` and ``.vector_score``) and ``.best_vec``."""
    from app.retrieve.search import retrieve

    return retrieve(
        session, org_id, query_text=query_text, query_vector=query_vector, topics=topics
    )


def select_related_facts(
    session: Session, org_id: uuid.UUID, candidates: Sequence[Any], topics: list[str]
) -> list[Fact]:
    """Owner C's ``select_facts`` (draft step 5)."""
    from app.retrieve.facts import select_facts

    return list(select_facts(session, org_id, candidates, topics))


def set_current_ai_version(
    session: Session, question: Question, answer: Answer, actor: str, confirm_displace: bool
) -> None:
    """Owner F's function: applies the displacement rule and moves status to ``ai_draft``
    through the transition function. The only path by which a draft becomes current."""
    from app.review.versions import set_current_ai_version as impl

    impl(session, question, answer, actor, confirm_displace)


def recompute_support(session: Session, answer: Answer) -> None:
    """Owner F's recompute (support_summary and needs_review). Until it lands, the shared
    arithmetic alone."""
    try:
        from app.review.support import recompute_support as impl
    except ImportError:
        answer.support_summary = summarise_support(answer.segments)
        return
    impl(session, answer)


# --- Displacement and serialisation -------------------------------------------------------------


def current_answer(session: Session, question: Question) -> Answer | None:
    return session.scalars(
        select(Answer).where(Answer.question_id == question.id, Answer.is_current.is_(True))
    ).first()


def serialise_answer(session: Session, answer: Answer) -> dict[str, Any]:
    """The full answer object as the API returns it, with ``verbatim`` built on read."""
    record = AnswerRecord.model_validate(answer).model_dump(mode="json")
    # The stored segments as they are (the schema dump would drop the span ``tier``), so the
    # client's state after ``done`` equals what GET returns and what the harness reads.
    record["segments"] = list(answer.segments)
    record["verbatim"] = verbatim_offer(session, answer.verbatim_offer_item_id)
    return record


def serialise_message(session: Session, message: Message) -> dict[str, Any]:
    record = ThreadMessage.model_validate(message).model_dump(mode="json")
    record["segments"] = list(message.segments) if message.segments is not None else None
    record["thread_id"] = str(message.thread_id)
    record["verbatim"] = verbatim_offer(session, message.verbatim_offer_item_id)
    return record


def check_displacement(session: Session, question: Question, confirm_displace: bool) -> None:
    """Raise ``Conflict409`` when an AI write would displace a version a person has stood
    behind (status past ``ai_draft``) and the request did not confirm it."""
    if question.status in _PAST_AI_DRAFT and not confirm_displace:
        current = current_answer(session, question)
        raise Conflict409(
            "displacement",
            "This question already has a version a person has stood behind. Confirm that "
            "the new draft may displace it.",
            current_answer=serialise_answer(session, current) if current is not None else None,
        )


# --- Candidate and fact context -----------------------------------------------------------------


@dataclass
class CandidateContext:
    item: KnowledgeItem
    section: DocumentSection
    document: Document | None
    vector_score: float | None = None

    @property
    def answer_slice(self) -> str:
        return self.section.text[self.item.answer_start : self.item.answer_end]


@dataclass
class FactContext:
    fact: Fact
    section: DocumentSection | None
    document: Document | None


def _load_candidates(session: Session, candidates: Sequence[Any]) -> list[CandidateContext]:
    contexts: list[CandidateContext] = []
    for candidate in candidates:
        item: KnowledgeItem = getattr(candidate, "item", candidate)
        section = session.get(DocumentSection, item.section_id)
        if section is None:
            logger.warning("candidate %s has no section %s; skipped", item.id, item.section_id)
            continue
        document = session.get(Document, item.document_id)
        contexts.append(
            CandidateContext(
                item=item,
                section=section,
                document=document,
                vector_score=getattr(candidate, "vector_score", None),
            )
        )
    return contexts


def _load_facts(session: Session, facts: Iterable[Fact]) -> list[FactContext]:
    return [
        FactContext(
            fact=fact,
            section=session.get(DocumentSection, fact.section_id),
            document=session.get(Document, fact.document_id),
        )
        for fact in facts
    ]


# --- Step 1: query rewrite ----------------------------------------------------------------------


def rewrite_query(question_text: str | None, history: History, latest: str) -> str:
    """Rewrite the latest turn into a standalone retrieval question (contextualisation only)."""
    settings = get_settings()
    parts: list[str] = []
    if question_text:
        parts.append(f"Tender question:\n{question_text}")
    if history:
        lines = [f"{turn['role']}: {turn['content']}" for turn in history]
        parts.append("Conversation so far:\n" + "\n\n".join(lines))
    parts.append(f"Latest user turn:\n{latest}")
    result = get_llm().parse(
        QUERY_REWRITE_PROMPT,
        model=settings.model_main,
        system=load_prompt(QUERY_REWRITE_PROMPT),
        user="\n\n".join(parts),
        output_model=QueryRewrite,
        max_tokens=1000,
    )
    query = result.query.strip()
    return query or latest


# --- Step 6: verbatim offer ---------------------------------------------------------------------


def verbatim_candidate(
    candidates: Sequence[CandidateContext], query_vector: Sequence[float]
) -> tuple[KnowledgeItem, float] | None:
    """The top candidate when it is a pair whose question-to-question cosine clears the
    verbatim threshold."""
    if not candidates:
        return None
    top = candidates[0].item
    if top.item_type != ItemType.QA_PAIR.value or top.question_embedding is None:
        return None
    similarity = cosine_similarity(
        [float(v) for v in query_vector], [float(v) for v in top.question_embedding]
    )
    if similarity < get_settings().verbatim_threshold:
        return None
    return top, round(similarity, 4)


# --- Step 7: synthesis prompt -------------------------------------------------------------------


def _candidate_block(context: CandidateContext) -> str:
    item = context.item
    lines = [f"[item:{item.id}] type={item.item_type}"]
    if item.item_type == ItemType.CHUNK.value:
        if context.section.heading_path:
            lines.append("Heading path: " + " > ".join(context.section.heading_path))
    elif item.question_text:
        lines.append(f"Past question: {item.question_text}")
    if context.document is not None:
        meta = [f"document: {context.document.filename}"]
        if context.document.doc_type:
            meta.append(f"type: {context.document.doc_type}")
        if context.document.doc_kind:
            meta.append(f"kind: {context.document.doc_kind}")
        if context.document.effective_date:
            meta.append(f"effective: {context.document.effective_date.isoformat()}")
        if context.document.buyer:
            meta.append(f"buyer: {context.document.buyer}")
        lines.append("(" + ", ".join(meta) + ")")
    lines.append("Text:")
    lines.append(context.answer_slice)
    return "\n".join(lines)


def _fact_block(context: FactContext) -> str:
    fact = context.fact
    parts = [
        f"[fact:{fact.id}] kind={fact.fact_kind}",
        f"key={fact.fact_key}" if fact.fact_key else None,
        f"value={fact.value}",
        f"effective={fact.effective_date.isoformat()}" if fact.effective_date else None,
        f"expires={fact.expires_on.isoformat()}" if fact.expires_on else None,
        f"document={context.document.filename}" if context.document is not None else None,
    ]
    header = " ".join(part for part in parts if part)
    return f"{header}\nStatement: {fact.statement}"


def build_synthesis_user_message(
    *,
    query_text: str,
    response_type: str,
    word_limit: int | None,
    buyer: str | None,
    instruction: str | None,
    candidates: Sequence[CandidateContext],
    facts: Sequence[FactContext],
) -> str:
    constraints = [f"Response type: {response_type}"]
    constraints.append(f"Word limit: {word_limit}" if word_limit else "Word limit: none stated")
    if buyer:
        constraints.append(f"Buyer: {buyer}")
    sections = [
        "## Question\n" + query_text,
        "## Constraints\n" + "\n".join(constraints),
    ]
    if instruction:
        sections.append("## Instruction from the user\n" + instruction)
    if candidates:
        sections.append(
            "## Candidates\n" + "\n\n".join(_candidate_block(context) for context in candidates)
        )
    else:
        sections.append("## Candidates\nNone retrieved.")
    if facts:
        sections.append("## Facts\n" + "\n\n".join(_fact_block(context) for context in facts))
    else:
        sections.append("## Facts\nNone.")
    return "\n\n".join(sections)


def _iter_lines(chunks: Iterable[str]) -> Iterator[str]:
    """Complete lines from a stream of text chunks; a trailing partial line is flushed."""
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line
    if buffer.strip():
        yield buffer


def parse_synthesis_line(line: str) -> SegmentLine | GapsLine | FactChecklistLine | None:
    """Validate one streamed line. A bad line is logged and skipped, never raised."""
    stripped = line.strip()
    if not stripped or stripped.startswith("```"):
        return None
    try:
        return SynthesisLine.validate_python(json.loads(stripped))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("synthesis line skipped (%s): %.200s", type(exc).__name__, stripped)
        return None


def parse_synthesis_document(text: str) -> list[SegmentLine | GapsLine | FactChecklistLine]:
    """Fallback for a model that ignored the one-object-per-line rule: parse the whole output
    as one JSON document. Accepts a single object with ``segments`` (and optional ``gaps`` and
    ``fact_checklist`` / ``fact_ids``) or an array of line objects; a fenced code block is
    unwrapped first. Returns the validated lines in order, or an empty list when nothing in
    the output can be read that way. Never raises."""
    body = text.strip()
    if body.startswith("```"):
        first_break = body.find("\n")
        body = body[first_break + 1 :] if first_break != -1 else ""
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
    body = body.strip()
    if not body:
        return []
    try:
        loaded = json.loads(body)
    except json.JSONDecodeError:
        return []

    raw_lines: list[Any]
    if isinstance(loaded, list):
        raw_lines = loaded
    elif isinstance(loaded, dict):
        raw_lines = []
        for segment in loaded.get("segments") or []:
            if isinstance(segment, dict):
                raw_lines.append({**segment, "type": "segment"})
        if isinstance(loaded.get("gaps"), list):
            raw_lines.append({"type": "gaps", "gaps": loaded["gaps"]})
        checklist = loaded.get("fact_checklist", loaded.get("fact_ids"))
        if isinstance(checklist, list):
            fact_ids = [
                entry.get("fact_id") if isinstance(entry, dict) else entry for entry in checklist
            ]
            raw_lines.append(
                {"type": "fact_checklist", "fact_ids": [str(fid) for fid in fact_ids if fid]}
            )
    else:
        return []

    parsed: list[SegmentLine | GapsLine | FactChecklistLine] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        try:
            parsed.append(SynthesisLine.validate_python(raw))
        except ValidationError as exc:
            logger.warning("synthesis fallback entry skipped (%s)", type(exc).__name__)
    if parsed:
        logger.warning(
            "synthesis output held no valid NDJSON lines; recovered %d entr%s from a "
            "whole-output parse",
            len(parsed),
            "y" if len(parsed) == 1 else "ies",
        )
    return parsed


# --- The pipeline -------------------------------------------------------------------------------


@dataclass
class DraftResult:
    segments: list[dict[str, Any]]  # verified, stored shape
    gaps: list[str]
    fact_checklist: list[dict[str, Any]]
    verbatim_offer_item_id: uuid.UUID | None
    retrieved_item_ids: list[str]
    rewritten_query: str | None
    model: str
    prompt_version: str
    candidates: list[CandidateContext] = field(default_factory=list)


def generate(
    session: Session,
    *,
    org_id: uuid.UUID,
    query_text: str,
    query_vector: list[float],
    topics: list[str],
    response_type: str,
    word_limit: int | None,
    buyer: str | None,
    instruction: str | None,
    history: History | None,
    rewritten_query: str | None,
    emit: Emit,
    offer_verbatim: bool = True,
) -> DraftResult:
    """Steps 2 to 9 for one query; emits ``verbatim``, ``segment``, ``gaps``,
    ``fact_checklist`` and ``support`` events. Persists nothing."""
    settings = get_settings()

    # Steps 2 to 4: retrieval (owner C), then the candidates' own text.
    retrieval = retrieve_candidates(
        session, org_id, query_text=query_text, query_vector=query_vector, topics=topics
    )
    raw_candidates = list(getattr(retrieval, "candidates", retrieval) or [])
    candidates = _load_candidates(session, raw_candidates)
    items_by_id = {str(context.item.id): context for context in candidates}

    # Step 5: facts (owner C).
    facts = _load_facts(session, select_related_facts(session, org_id, raw_candidates, topics))
    facts_by_id = {str(context.fact.id): context for context in facts}

    # Step 6: verbatim offer, before any segment.
    verbatim_item_id: uuid.UUID | None = None
    if offer_verbatim:
        offer = verbatim_candidate(candidates, query_vector)
        if offer is not None:
            item, similarity = offer
            verbatim_item_id = item.id
            emit(
                {
                    "type": "verbatim",
                    "source_item_id": str(item.id),
                    "similarity": similarity,
                    "segments": build_verbatim_segments(session, item),
                }
            )

    # Step 7 and 8: streamed synthesis, conformed on the stream.
    user_message = build_synthesis_user_message(
        query_text=query_text,
        response_type=response_type,
        word_limit=word_limit,
        buyer=buyer,
        instruction=instruction,
        candidates=candidates,
        facts=facts,
    )
    stream = get_llm().stream_text(
        SYNTHESIS_PROMPT,
        model=settings.model_main,
        system=load_prompt(SYNTHESIS_PROMPT),
        user=user_message,
        history=history or None,
    )
    conformer = Conformer()
    wire_segments: list[dict[str, Any]] = []
    gaps: list[str] = []
    listed_fact_ids: list[str] = []

    def emit_segments(segments: Iterable[Any]) -> None:
        for segment in segments:
            record = segment.to_dict()
            record["support_status"] = WIRE_PENDING
            wire_segments.append(record)
            emit({"type": "segment", **record})

    def consume(parsed: SegmentLine | GapsLine | FactChecklistLine) -> None:
        nonlocal gaps, listed_fact_ids
        if isinstance(parsed, SegmentLine):
            sources = _fill_sources(parsed.sources, items_by_id, facts_by_id)
            emit_segments(
                conformer.feed(
                    {
                        "text": parsed.text,
                        "paragraph": parsed.paragraph,
                        "kind": parsed.kind,
                        "sources": sources,
                    }
                )
            )
        elif isinstance(parsed, GapsLine):
            gaps = [gap.strip() for gap in parsed.gaps if gap and gap.strip()]
        else:
            listed_fact_ids = [fact_id for fact_id in parsed.fact_ids if fact_id]

    raw_lines: list[str] = []
    valid_lines = 0
    for line in _iter_lines(stream):
        raw_lines.append(line)
        parsed = parse_synthesis_line(line)
        if parsed is None:
            continue
        valid_lines += 1
        consume(parsed)
    if valid_lines == 0:
        # The model ignored the one-object-per-line rule (for example pretty-printed JSON):
        # try the whole output once before giving up.
        for parsed in parse_synthesis_document("\n".join(raw_lines)):
            consume(parsed)
    emit_segments(conformer.flush())

    if not wire_segments:
        raise DraftError("empty_draft", "The model returned no usable sentences.")

    emit({"type": "gaps", "gaps": gaps})

    cited_fact_ids = [
        source["source_id"]
        for segment in wire_segments
        for source in segment["sources"]
        if source.get("source_type") == SourceType.FACT.value
    ]
    fact_checklist = verify_fact_checklist(session, [*listed_fact_ids, *cited_fact_ids])
    emit({"type": "fact_checklist", "fact_checklist": fact_checklist})

    # Step 9: support verification, one support event per segment.
    verified = verify_segments(session, wire_segments)
    for segment in verified:
        emit(
            {
                "type": "support",
                "index": segment["index"],
                "support_status": segment["support_status"],
                "sources": segment["sources"],
            }
        )

    return DraftResult(
        segments=verified,
        gaps=gaps,
        fact_checklist=fact_checklist,
        verbatim_offer_item_id=verbatim_item_id,
        retrieved_item_ids=[str(context.item.id) for context in candidates],
        rewritten_query=rewritten_query,
        model=settings.model_main,
        prompt_version=prompt_version(SYNTHESIS_PROMPT),
        candidates=candidates,
    )


def _fill_sources(
    sources: Sequence[ModelSource],
    items_by_id: dict[str, CandidateContext],
    facts_by_id: dict[str, FactContext],
) -> list[dict[str, Any]]:
    """Attach locator (offsets null), document title, type, kind and date to each cited source
    from the cited row. Sources naming an id that was not offered are dropped."""
    filled: list[dict[str, Any]] = []
    for source in sources:
        source_id = source.source_id.strip()
        for prefix in ("item:", "fact:"):
            if source_id.startswith(prefix):
                source_id = source_id[len(prefix) :]
        if source.source_type == SourceType.KNOWLEDGE_ITEM.value:
            context = items_by_id.get(source_id)
            if context is None:
                logger.warning("segment cites an item not offered to the model: %s", source_id)
                continue
            filled.append(
                source_record(
                    source_type=SourceType.KNOWLEDGE_ITEM.value,
                    source_id=context.item.id,
                    quote=source.quote,
                    section=context.section,
                    document=context.document,
                )
            )
        else:
            fact_context = facts_by_id.get(source_id)
            if fact_context is None or fact_context.section is None:
                logger.warning("segment cites a fact not offered to the model: %s", source_id)
                continue
            filled.append(
                source_record(
                    source_type=SourceType.FACT.value,
                    source_id=fact_context.fact.id,
                    quote=source.quote,
                    section=fact_context.section,
                    document=fact_context.document,
                )
            )
    return filled


# --- Step 10: persistence -----------------------------------------------------------------------


def persist_ai_answer(
    session: Session,
    question: Question,
    *,
    actor: str,
    confirm_displace: bool,
    segments: list[dict[str, Any]],
    gaps: list[str],
    fact_checklist: list[dict[str, Any]],
    model: str | None,
    prompt_version: str | None,
    verbatim_offer_item_id: uuid.UUID | None = None,
    verbatim_source_item_id: uuid.UUID | None = None,
    event_type: str | EventType = EventType.ANSWER_CREATED,
    event_payload: dict[str, Any] | None = None,
) -> Answer:
    """Create a new ``ai`` version, make it current through the review owner's function
    (displacement rule, status to ``ai_draft``), recompute support and write the event.
    Flushes; the caller commits.

    The question row is re-read under a row lock first: a card draft loads the question
    before a synthesis call that takes many seconds, and a human edit saved meanwhile must
    not be displaced on the strength of a stale ``status``. The lock is held until the caller
    commits, so the displacement check, ``make_current`` and the transition all see the
    committed row, and a concurrent writer waits rather than interleaving.
    """
    session.refresh(question, with_for_update=True)
    check_displacement(session, question, confirm_displace)
    text = join_segments(segments)
    word_count = len(text.split())
    next_version = (
        session.execute(
            select(func.coalesce(func.max(Answer.version), 0)).where(
                Answer.question_id == question.id
            )
        ).scalar_one()
        + 1
    )
    answer = Answer(
        org_id=question.org_id,
        question_id=question.id,
        version=next_version,
        author_type=AuthorType.AI.value,
        author_name=None,
        text=text,
        word_count=word_count,
        segments=segments,
        gaps=list(gaps),
        fact_checklist=list(fact_checklist),
        verbatim_offer_item_id=verbatim_offer_item_id,
        verbatim_source_item_id=verbatim_source_item_id,
        support_summary=summarise_support(segments),
        model=model,
        prompt_version=prompt_version,
        is_current=False,
    )
    session.add(answer)
    session.flush()
    set_current_ai_version(session, question, answer, actor, confirm_displace)
    recompute_support(session, answer)
    payload: dict[str, Any] = {
        "answer_id": str(answer.id),
        "version": answer.version,
        "author_type": AuthorType.AI.value,
        "model": model,
        "prompt_version": prompt_version,
        "word_count": word_count,
    }
    if question.word_limit and word_count > question.word_limit:
        # The word limit is checked and reported; the draft is not truncated.
        payload["word_limit"] = question.word_limit
        payload["word_limit_exceeded"] = True
        logger.info(
            "draft for question %s is %d words against a limit of %d",
            question.id,
            word_count,
            question.word_limit,
        )
    payload.update(event_payload or {})
    record_event(
        session,
        EntityType.QUESTION,
        question.id,
        event_type,
        actor,
        payload,
        org_id=question.org_id,
    )
    session.flush()
    return answer


# --- Entry points -------------------------------------------------------------------------------


def draft_question(
    session: Session,
    question: Question,
    *,
    actor: str,
    instruction: str | None = None,
    confirm_displace: bool = False,
    emit: Emit | None = None,
) -> Answer:
    """A card draft: the whole pipeline for ``question``, persisted as a new current ``ai``
    version. Raises ``Conflict409`` for a pricing question or a displacement without the flag.
    Flushes; the caller commits and emits ``done`` with ``serialise_answer``."""
    emit = emit or _no_emit
    if question.response_type == ResponseType.PRICING.value:
        raise Conflict409("pricing", "Pricing questions are never drafted.")
    check_displacement(session, question, confirm_displace)
    query_vector = query_vector_for(session, question=question)
    buyer = question.tender.buyer if question.tender is not None else None
    result = generate(
        session,
        org_id=question.org_id,
        query_text=question.text,
        query_vector=query_vector,
        topics=list(question.topics or []),
        response_type=question.response_type,
        word_limit=question.word_limit,
        buyer=buyer,
        instruction=instruction,
        history=None,
        rewritten_query=None,
        emit=emit,
    )
    return persist_ai_answer(
        session,
        question,
        actor=actor,
        confirm_displace=confirm_displace,
        segments=result.segments,
        gaps=result.gaps,
        fact_checklist=result.fact_checklist,
        model=result.model,
        prompt_version=result.prompt_version,
        verbatim_offer_item_id=result.verbatim_offer_item_id,
        event_payload={"retrieved_item_ids": result.retrieved_item_ids},
    )


def llm_history(messages: Iterable[Message]) -> History:
    """Prior turns for the model: user and assistant messages, consecutive same-role turns
    merged, starting with a user turn."""
    history: History = []
    for message in messages:
        if message.role not in (MessageRole.USER.value, MessageRole.ASSISTANT.value):
            continue
        if not history and message.role != MessageRole.USER.value:
            continue
        if history and history[-1]["role"] == message.role:
            history[-1]["content"] += "\n\n" + message.content
        else:
            history.append({"role": message.role, "content": message.content})
    return history


def persist_user_message(session: Session, thread: Thread, content: str) -> Message:
    """Store the user's turn. Flushes; the caller commits it before the pipeline runs so it
    remains when the reply fails."""
    message = Message(
        org_id=thread.org_id,
        thread_id=thread.id,
        role=MessageRole.USER.value,
        content=content,
    )
    session.add(message)
    session.flush()
    return message


def reply_in_thread(
    session: Session,
    thread: Thread,
    *,
    content: str,
    actor: str,
    emit: Emit | None = None,
    user_message: Message | None = None,
) -> Message:
    """A thread reply: history from the thread, the query rewritten when there is history,
    the same pipeline, persisted as an assistant message. ``user_message`` is the already
    stored user turn (the runner persists and commits it first); when ``None`` it is stored
    here. Flushes; the caller commits and emits ``done`` with ``serialise_message``."""
    emit = emit or _no_emit
    question = thread.question
    if question is not None and question.response_type == ResponseType.PRICING.value:
        # Pricing questions receive no AI draft in any form; the runner refuses before the
        # user message is stored, this guard covers direct callers.
        raise Conflict409("pricing", "Pricing questions are never drafted.")
    if user_message is None:
        user_message = persist_user_message(session, thread, content)
    prior = [message for message in thread.messages if message.id != user_message.id]
    history = llm_history(prior)

    rewritten: str | None = None
    if history:
        rewritten = rewrite_query(question.text if question is not None else None, history, content)
        query_text = rewritten
        query_vector = query_vector_for(session, text=rewritten)
    elif question is not None:
        query_text = question.text
        query_vector = query_vector_for(session, question=question)
    else:
        query_text = content
        query_vector = query_vector_for(session, text=content)

    if question is not None:
        response_type = question.response_type
        topics = list(question.topics or [])
        word_limit = question.word_limit
    else:
        response_type = ResponseType.FREE_TEXT.value
        topics = []  # free text: no topic list, only facts attached to the retrieved items
        word_limit = None
    tender = question.tender if question is not None else session.get(Tender, thread.tender_id)
    buyer = tender.buyer if tender is not None else None

    result = generate(
        session,
        org_id=thread.org_id,
        query_text=query_text,
        query_vector=query_vector,
        topics=topics,
        response_type=response_type,
        word_limit=word_limit,
        buyer=buyer,
        instruction=content,
        history=history,
        rewritten_query=rewritten,
        emit=emit,
    )
    message = Message(
        org_id=thread.org_id,
        thread_id=thread.id,
        role=MessageRole.ASSISTANT.value,
        content=join_segments(result.segments),
        segments=result.segments,
        rewritten_query=result.rewritten_query,
        retrieved_item_ids=result.retrieved_item_ids,
        support_summary=summarise_support(result.segments),
        gaps=result.gaps,
        fact_checklist=result.fact_checklist,
        verbatim_offer_item_id=result.verbatim_offer_item_id,
        model=result.model,
        prompt_version=result.prompt_version,
    )
    session.add(message)
    session.flush()
    return message


__all__ = [
    "CandidateContext",
    "DraftResult",
    "FactChecklistLine",
    "FactContext",
    "GapsLine",
    "ModelSource",
    "QueryRewrite",
    "SegmentLine",
    "build_synthesis_user_message",
    "check_displacement",
    "current_answer",
    "draft_question",
    "generate",
    "llm_history",
    "parse_synthesis_line",
    "persist_ai_answer",
    "persist_user_message",
    "query_vector_for",
    "recompute_support",
    "reply_in_thread",
    "retrieve_candidates",
    "rewrite_query",
    "select_related_facts",
    "serialise_answer",
    "serialise_message",
    "set_current_ai_version",
    "verbatim_candidate",
]

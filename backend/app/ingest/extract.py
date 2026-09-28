"""Step 3: extract question-answer pairs from a past submission by anchors.

One LLM call per window of consecutive sections (sized by ``pair_extraction_window_tokens``
with one section of overlap). The server locates each pair's anchors in the named sections
after normalisation and slices the text out; the model never retypes an answer into storage.

Disambiguation (plan, ingestion step 3): a start anchor matching more than once takes the
first match after the end of the previous pair in that section; an end anchor takes the
nearest match after the chosen start; a slice whose normalised length differs from the
normalised copied text's by more than a quarter of the latter is treated as not located; an
answer lies within one section. When an anchor is not located, ``find_span`` runs on the
copied text; if that fails too the copied text is stored with ``text_verified = false``.
Pairs returned twice because of the overlap are collapsed when their answer anchors resolve
to the same offsets. Fragments that cannot be paired go to ``unpaired_fragments``.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import enums as e
from app.db.models import Document, DocumentSection, KnowledgeItem, Organisation, UnpairedFragment
from app.errors import format_error
from app.ingest.normalise import find_span, normalise, to_original_span
from app.ingest.types import (
    ExtractedFact,
    ExtractedPair,
    ExtractionResult,
    FailedWindow,
    PairExtractionOutput,
    RawFact,
)
from app.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "extract_pairs"
# Rough per-section overhead for the id label and heading path in the prompt.
_LABEL_TOKENS = 30


class ExtractionFailed(RuntimeError):
    """At least one window's LLM call failed. Raised after every window has been attempted so
    the stage fails as a whole and the worker's retry rule applies; a document never reaches
    ``ready`` with the pairs and facts of a failed window silently missing."""


# --- Windows ------------------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Characters over four, the plan's estimate."""
    return max(1, len(text) // 4)


def build_windows(
    sections: Sequence[DocumentSection], window_tokens: int | None = None
) -> list[list[DocumentSection]]:
    """Consecutive sections grouped to about ``window_tokens`` with one section of overlap:
    the last section of a window is the first of the next."""
    limit = window_tokens or get_settings().pair_extraction_window_tokens
    windows: list[list[DocumentSection]] = []
    current: list[DocumentSection] = []
    used = 0
    for section in sections:
        cost = estimate_tokens(section.text) + _LABEL_TOKENS
        if current and used + cost > limit:
            windows.append(current)
            overlap = current[-1]
            current = [overlap]
            used = estimate_tokens(overlap.text) + _LABEL_TOKENS
        current.append(section)
        used += cost
    if current:
        windows.append(current)
    return windows


# --- Prompt -------------------------------------------------------------------------------------


def _render_section(section: DocumentSection) -> str:
    path = " > ".join(section.heading_path) if section.heading_path else "(none)"
    location = f" | row {section.cell_ref}" if section.cell_ref else ""
    return (
        f"<<< section id={section.id} | order {section.order_index} | "
        f"heading path: {path}{location} >>>\n{section.text}"
    )


def build_window_prompt(
    document: Document, window: Sequence[DocumentSection], taxonomy: Sequence[str]
) -> str:
    settings = get_settings()
    kinds = "\n".join(
        f"- `{kind.name}` (topic `{kind.topic}`): key rule: {kind.key_rule}"
        for kind in settings.fact_kinds.values()
    )
    rendered = "\n\n".join(_render_section(section) for section in window)
    return (
        f"Document: {document.filename}\n"
        f"Buyer (if known): {document.buyer or 'not stated'}\n\n"
        f"Topic taxonomy: {', '.join(f'`{topic}`' for topic in taxonomy)}\n\n"
        f"Fact kinds:\n{kinds}\n\n"
        f"Sections in this window ({len(window)}), in document order. "
        f"Use the ids exactly as labelled.\n\n{rendered}"
    )


# --- Anchor location ----------------------------------------------------------------------------


def locate_by_anchors(
    section_text: str,
    start_anchor: str,
    end_anchor: str,
    copied_text: str,
    *,
    search_from: int = 0,
) -> tuple[int, int] | None:
    """Locate ``copied_text`` in ``section_text`` by its anchors, per the plan's rules.

    ``search_from`` is an original offset: the end of the previous pair in this section.
    Returns original offsets (``end`` exclusive) or None when not located.
    """
    text_norm, index = normalise(section_text)
    start_norm, _ = normalise(start_anchor)
    end_norm, _ = normalise(end_anchor)
    copied_norm, _ = normalise(copied_text)
    if not text_norm or not start_norm or not end_norm or not copied_norm:
        return None
    from_norm = bisect_left(index, search_from) if search_from > 0 else 0
    start = text_norm.find(start_norm, from_norm)
    if start == -1:
        return None
    end_at = text_norm.find(end_norm, start)
    if end_at == -1:
        return None
    end = end_at + len(end_norm)
    if abs((end - start) - len(copied_norm)) > len(copied_norm) / 4:
        return None
    return to_original_span(index, start, end)


@dataclass(frozen=True)
class LocatedSpan:
    start: int
    end: int
    by_anchors: bool


def locate_text(
    section_text: str,
    start_anchor: str,
    end_anchor: str,
    copied_text: str,
    *,
    search_from: int = 0,
) -> LocatedSpan | None:
    """Anchors first; ``find_span`` on the copied text as the fallback."""
    span = locate_by_anchors(
        section_text, start_anchor, end_anchor, copied_text, search_from=search_from
    )
    if span is not None:
        return LocatedSpan(span[0], span[1], by_anchors=True)
    if not copied_text.strip():
        return None
    match = find_span(copied_text, section_text)
    if match is None:
        return None
    return LocatedSpan(match.start, match.end, by_anchors=False)


# --- Validation helpers -------------------------------------------------------------------------


def clean_topics(topics: Sequence[str], taxonomy: Sequence[str]) -> list[str]:
    allowed = set(taxonomy)
    seen: list[str] = []
    for topic in topics:
        value = (topic or "").strip()
        if value in allowed and value not in seen:
            seen.append(value)
    return seen


def to_raw_facts(
    facts: Sequence[ExtractedFact], *, section_id, knowledge_item_id  # noqa: ANN001
) -> list[RawFact]:
    """Keep facts of a configured kind with a statement and a value; null the key where the
    kind has one current holder."""
    settings = get_settings()
    out: list[RawFact] = []
    for fact in facts:
        kind = settings.fact_kinds.get((fact.fact_kind or "").strip())
        if kind is None:
            logger.info("dropping fact of unknown kind %r", fact.fact_kind)
            continue
        statement = (fact.statement or "").strip()
        value = (fact.value or "").strip()
        if not statement or not value:
            continue
        key = (fact.fact_key or "").strip() or None
        out.append(
            RawFact(
                fact_kind=kind.name,
                fact_key=key if kind.keyed else None,
                statement=statement,
                value=value,
                effective_date=fact.effective_date,
                expires_on=fact.expires_on,
                section_id=section_id,
                knowledge_item_id=knowledge_item_id,
            )
        )
    return out


def _taxonomy(session: Session, document: Document) -> list[str]:
    organisation = session.get(Organisation, document.org_id)
    if organisation is not None and organisation.topic_taxonomy:
        return list(organisation.topic_taxonomy)
    return list(get_settings().default_topic_taxonomy)


# --- Pair resolution ----------------------------------------------------------------------------


@dataclass
class ResolvedPair:
    answer_section: DocumentSection
    answer_span: LocatedSpan | None
    answer_text: str
    question_section: DocumentSection | None
    question_span: LocatedSpan | None
    question_text: str
    topics: list[str]
    facts: list[ExtractedFact]

    @property
    def text_verified(self) -> bool:
        return self.answer_span is not None

    @property
    def dedup_key(self) -> tuple:
        if self.answer_span is not None:
            return ("span", self.answer_section.id, self.answer_span.start, self.answer_span.end)
        return ("copy", self.answer_section.id, normalise(self.answer_text)[0])


def _find_section_for_copy(
    copied: str, window: Sequence[DocumentSection]
) -> tuple[DocumentSection, LocatedSpan] | None:
    """When the model names a section id that does not exist, look for the copied text in
    the window's sections instead."""
    if not copied.strip():
        return None
    for section in window:
        match = find_span(copied, section.text)
        if match is not None:
            return section, LocatedSpan(match.start, match.end, by_anchors=False)
    return None


def resolve_pair(
    pair: ExtractedPair,
    by_id: dict[str, DocumentSection],
    window: Sequence[DocumentSection],
    cursors: dict[str, int],
    taxonomy: Sequence[str],
) -> ResolvedPair | None:
    """Locate one pair's question and answer. Updates ``cursors`` (end of the last located
    span per section) so the next pair's start anchor is searched after this one."""
    answer_section = by_id.get(pair.answer_section_id.strip())
    answer_span: LocatedSpan | None = None
    if answer_section is None:
        found = _find_section_for_copy(pair.answer_text_copy, window)
        if found is None:
            logger.warning(
                "pair names unknown answer section %r and its copy was not found; dropping",
                pair.answer_section_id,
            )
            return None
        answer_section, answer_span = found
    else:
        answer_span = locate_text(
            answer_section.text,
            pair.answer_start_anchor,
            pair.answer_end_anchor,
            pair.answer_text_copy,
            search_from=cursors.get(str(answer_section.id), 0),
        )
    answer_text = (
        answer_section.text[answer_span.start : answer_span.end]
        if answer_span is not None
        else pair.answer_text_copy.strip()
    )
    if not answer_text.strip():
        return None

    question_section = by_id.get(pair.question_section_id.strip())
    question_span: LocatedSpan | None = None
    if question_section is None:
        found = _find_section_for_copy(pair.question_text_copy, window)
        if found is not None:
            question_section, question_span = found
    else:
        question_span = locate_text(
            question_section.text,
            pair.question_start_anchor,
            pair.question_end_anchor,
            pair.question_text_copy,
            search_from=cursors.get(str(question_section.id), 0),
        )
    question_text = (
        question_section.text[question_span.start : question_span.end]
        if question_section is not None and question_span is not None
        else pair.question_text_copy.strip()
    )

    for section, span in ((answer_section, answer_span), (question_section, question_span)):
        if section is not None and span is not None:
            key = str(section.id)
            cursors[key] = max(cursors.get(key, 0), span.end)

    return ResolvedPair(
        answer_section=answer_section,
        answer_span=answer_span,
        answer_text=answer_text,
        question_section=question_section,
        question_span=question_span,
        question_text=question_text,
        topics=clean_topics(pair.topics, taxonomy),
        facts=list(pair.facts),
    )


def _make_item(document: Document, resolved: ResolvedPair) -> KnowledgeItem:
    same_section = (
        resolved.question_section is None
        or resolved.question_section.id == resolved.answer_section.id
    )
    return KnowledgeItem(
        org_id=document.org_id,
        document_id=document.id,
        section_id=resolved.answer_section.id,
        answer_start=resolved.answer_span.start if resolved.answer_span else 0,
        answer_end=resolved.answer_span.end if resolved.answer_span else 0,
        question_section_id=None if same_section else resolved.question_section.id,
        question_start=resolved.question_span.start if resolved.question_span else None,
        question_end=resolved.question_span.end if resolved.question_span else None,
        item_type=e.ItemType.QA_PAIR.value,
        question_text=resolved.question_text or None,
        answer_text=resolved.answer_text,
        text_verified=resolved.text_verified,
        topics=resolved.topics,
        is_canonical=False,
        excluded_from_retrieval=False,
    )


# --- Entry point --------------------------------------------------------------------------------


def extract_pairs(
    session: Session, document: Document, sections: Sequence[DocumentSection]
) -> ExtractionResult:
    """Run step 3 over every window. Items and fragments are added and flushed; not committed.

    An LLM failure on one window is logged and recorded in ``result.failed_windows``, and the
    remaining windows are still attempted so every failure is seen in one run. Once all
    windows have run, ``ExtractionFailed`` is raised if any failed: the stage fails as a
    whole, the worker rolls back the flushed partial output and requeues the job below
    ``max_attempts`` (the resume re-runs the stage from clean), and after the last attempt the
    document is marked ``failed`` with the error, so a short pair count is never presented as
    a ``ready`` document.
    """
    settings = get_settings()
    taxonomy = _taxonomy(session, document)
    by_id = {str(section.id): section for section in sections}
    system = load_prompt(PROMPT_NAME)
    llm = get_llm()

    result = ExtractionResult()
    seen_pairs: set[tuple] = set()
    seen_fragments: set[tuple] = set()
    anchored = 0

    windows = build_windows(sections, settings.pair_extraction_window_tokens)
    for window_index, window in enumerate(windows):
        try:
            output = llm.parse(
                PROMPT_NAME,
                model=settings.model_main,
                system=system,
                user=build_window_prompt(document, window, taxonomy),
                output_model=PairExtractionOutput,
                max_tokens=32000,
            )
        except Exception as exc:  # noqa: BLE001 - isolate the window; the stage decides below
            logger.exception(
                "%s: pair extraction failed on window %d of %d (%d section(s))",
                document.filename,
                window_index + 1,
                len(windows),
                len(window),
            )
            result.failed_windows.append(
                FailedWindow(
                    index=window_index,
                    section_ids=[section.id for section in window],
                    error=format_error(exc),  # reaches jobs.error via ExtractionFailed
                )
            )
            continue
        # Cursors restart per window so a pair repeated across the overlap resolves to the
        # same offsets and is collapsed below.
        cursors: dict[str, int] = {}
        for pair in output.pairs:
            resolved = resolve_pair(pair, by_id, window, cursors, taxonomy)
            if resolved is None:
                continue
            key = resolved.dedup_key
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            item = _make_item(document, resolved)
            session.add(item)
            session.flush()
            result.items.append(item)
            if resolved.answer_span is not None and resolved.answer_span.by_anchors:
                anchored += 1
            result.raw_facts.extend(
                to_raw_facts(
                    resolved.facts,
                    section_id=resolved.answer_section.id,
                    knowledge_item_id=item.id,
                )
            )

        for fragment in output.fragments:
            section = by_id.get(fragment.section_id.strip())
            text = (fragment.text or "").strip()
            if section is None or not text:
                continue
            key = (section.id, normalise(text)[0])
            if key in seen_fragments:
                continue
            seen_fragments.add(key)
            match = find_span(text, section.text)
            row = UnpairedFragment(
                org_id=document.org_id,
                document_id=document.id,
                section_id=section.id,
                role=e.FragmentRole(fragment.role).value,
                text=section.text[match.start : match.end] if match else text,
                start=match.start if match else None,
                end=match.end if match else None,
            )
            session.add(row)
            result.fragments.append(row)

    session.flush()
    if result.failed_windows:
        failed = result.failed_windows
        section_ids = ", ".join(
            str(section_id) for window in failed for section_id in window.section_ids
        )
        raise ExtractionFailed(
            f"Pair extraction failed on {len(failed)} of {len(windows)} window(s) of "
            f"{document.filename} (window(s) {', '.join(str(w.index + 1) for w in failed)}; "
            f"section(s) {section_ids}): {failed[0].error}"
        )
    verified = sum(1 for item in result.items if item.text_verified)
    logger.info(
        "%s: %d pair(s) extracted, %d verified (%d by anchors), %d fragment(s), %d fact(s), "
        "%d of %d window(s) failed",
        document.filename,
        len(result.items),
        verified,
        anchored,
        len(result.fragments),
        len(result.raw_facts),
        len(result.failed_windows),
        len(windows),
    )
    return result


__all__ = [
    "ExtractionFailed",
    "LocatedSpan",
    "ResolvedPair",
    "build_window_prompt",
    "build_windows",
    "clean_topics",
    "estimate_tokens",
    "extract_pairs",
    "locate_by_anchors",
    "locate_text",
    "resolve_pair",
    "to_raw_facts",
]

"""Step 4: chunk a reference document by section into contiguous slices of about
``chunk_target_tokens`` tokens.

The stored ``answer_text`` is the raw slice ``section.text[answer_start:answer_end]``, so
``text_verified`` is true by construction and no check is run. The heading path is prepended
only to the string passed to ``embed()`` (``embedding_input``); it is never stored. Topics and
facts are emitted per chunk in the step 3 shape by one LLM call per batch of chunks.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import enums as e
from app.db.models import Document, DocumentSection, KnowledgeItem, Organisation
from app.ingest.extract import _LABEL_TOKENS, clean_topics, estimate_tokens, to_raw_facts
from app.ingest.types import ChunkAnnotationOutput, ChunkResult
from app.llm import get_llm, load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "chunk_annotate"  # versioned in app.llm.prompts.PROMPT_VERSIONS

_SENTENCE_END_RE = re.compile(r"[.!?…][\"'”’)\]]*\s")
_CHARS_PER_TOKEN = 4


# --- Slicing ------------------------------------------------------------------------------------


def _best_break(text: str, low: int, high: int) -> int:
    """The latest good break point in ``[low, high)``: a paragraph break, then a sentence end,
    then a space; ``high`` when the range holds none."""
    window = text[low:high]
    paragraph = window.rfind("\n")
    if paragraph > 0:
        return low + paragraph + 1
    last_sentence = None
    for match in _SENTENCE_END_RE.finditer(window):
        last_sentence = match.end()
    if last_sentence is not None and last_sentence > 0:
        return low + last_sentence
    space = window.rfind(" ")
    if space > 0:
        return low + space + 1
    return high


def chunk_offsets(text: str, target_chars: int | None = None) -> list[tuple[int, int]]:
    """Contiguous ``(start, end)`` slices of ``text`` (code-point offsets, ``end`` exclusive),
    each trimmed of surrounding whitespace, of roughly ``target_chars`` characters."""
    target = target_chars or get_settings().chunk_target_tokens * _CHARS_PER_TOKEN
    target = max(target, 8)
    length = len(text)
    spans: list[tuple[int, int]] = []
    position = 0
    while position < length:
        while position < length and text[position].isspace():
            position += 1
        if position >= length:
            break
        remaining = length - position
        if remaining <= int(target * 1.5):
            end = length
        else:
            low = position + int(target * 0.5)
            high = min(length, position + int(target * 1.25))
            end = _best_break(text, low, high)
            if end <= position:
                end = min(length, position + target)
        trimmed = end
        while trimmed > position and text[trimmed - 1].isspace():
            trimmed -= 1
        if trimmed > position:
            spans.append((position, trimmed))
        position = end
    return spans


def embedding_input(item: KnowledgeItem, section: DocumentSection) -> str:
    """The string ``embed()`` receives for a chunk: the heading path, then the raw slice.
    Never stored. Owner B calls this in ``embed_items`` for ``item_type = chunk``."""
    if section.heading_path:
        return " > ".join(section.heading_path) + "\n" + item.answer_text
    return item.answer_text


# --- Annotation ---------------------------------------------------------------------------------


def _taxonomy(session: Session, document: Document) -> list[str]:
    organisation = session.get(Organisation, document.org_id)
    if organisation is not None and organisation.topic_taxonomy:
        return list(organisation.topic_taxonomy)
    return list(get_settings().default_topic_taxonomy)


def _batches(
    items: Sequence[tuple[KnowledgeItem, DocumentSection]], window_tokens: int
) -> list[list[tuple[KnowledgeItem, DocumentSection]]]:
    batches: list[list[tuple[KnowledgeItem, DocumentSection]]] = []
    current: list[tuple[KnowledgeItem, DocumentSection]] = []
    used = 0
    for pair in items:
        cost = estimate_tokens(pair[0].answer_text) + _LABEL_TOKENS
        if current and used + cost > window_tokens:
            batches.append(current)
            current, used = [], 0
        current.append(pair)
        used += cost
    if current:
        batches.append(current)
    return batches


def build_annotation_prompt(
    document: Document,
    batch: Sequence[tuple[KnowledgeItem, DocumentSection]],
    taxonomy: Sequence[str],
) -> str:
    settings = get_settings()
    kinds = "\n".join(
        f"- `{kind.name}` (topic `{kind.topic}`): key rule: {kind.key_rule}"
        for kind in settings.fact_kinds.values()
    )
    rendered = "\n\n".join(
        f"<<< chunk id={item.id} | heading path: "
        f"{' > '.join(section.heading_path) if section.heading_path else '(none)'} >>>\n"
        f"{item.answer_text}"
        for item, section in batch
    )
    return (
        f"Document: {document.filename} (kind: {document.doc_kind or 'unknown'}, "
        f"effective date stated: {document.effective_date or 'unknown'})\n\n"
        f"Topic taxonomy: {', '.join(f'`{topic}`' for topic in taxonomy)}\n\n"
        f"Fact kinds:\n{kinds}\n\n"
        f"Chunks ({len(batch)}). Use the ids exactly as labelled.\n\n{rendered}"
    )


def annotate_chunks(
    session: Session,
    document: Document,
    items: Sequence[tuple[KnowledgeItem, DocumentSection]],
) -> list:
    """Topics and facts for every chunk; sets ``topics`` on the items and returns RawFacts."""
    if not items:
        return []
    settings = get_settings()
    taxonomy = _taxonomy(session, document)
    system = load_prompt(PROMPT_NAME)
    llm = get_llm()
    by_id = {str(item.id): (item, section) for item, section in items}
    raw_facts: list = []
    for batch in _batches(items, settings.pair_extraction_window_tokens):
        output = llm.parse(
            PROMPT_NAME,
            model=settings.model_fast,
            system=system,
            user=build_annotation_prompt(document, batch, taxonomy),
            output_model=ChunkAnnotationOutput,
            max_tokens=16000,
        )
        for annotation in output.chunks:
            found = by_id.get(annotation.chunk_id.strip())
            if found is None:
                logger.info("annotation names unknown chunk %r; ignored", annotation.chunk_id)
                continue
            item, section = found
            item.topics = clean_topics(annotation.topics, taxonomy)
            raw_facts.extend(
                to_raw_facts(
                    annotation.facts, section_id=section.id, knowledge_item_id=item.id
                )
            )
    session.flush()
    return raw_facts


# --- Entry point --------------------------------------------------------------------------------


def chunk_reference(
    session: Session, document: Document, sections: Sequence[DocumentSection]
) -> ChunkResult:
    """Run step 4. Chunk items are added and flushed; not committed."""
    settings = get_settings()
    target_chars = settings.chunk_target_tokens * _CHARS_PER_TOKEN
    pairs: list[tuple[KnowledgeItem, DocumentSection]] = []
    for section in sections:
        for start, end in chunk_offsets(section.text, target_chars):
            item = KnowledgeItem(
                org_id=document.org_id,
                document_id=document.id,
                section_id=section.id,
                answer_start=start,
                answer_end=end,
                question_section_id=None,
                question_start=None,
                question_end=None,
                item_type=e.ItemType.CHUNK.value,
                question_text=None,
                answer_text=section.text[start:end],
                text_verified=True,
                topics=[],
                is_canonical=False,
                excluded_from_retrieval=False,
            )
            session.add(item)
            pairs.append((item, section))
    session.flush()
    raw_facts = annotate_chunks(session, document, pairs)
    logger.info(
        "%s: %d chunk(s) from %d section(s), %d fact(s)",
        document.filename,
        len(pairs),
        len(sections),
        len(raw_facts),
    )
    return ChunkResult(items=[item for item, _ in pairs], raw_facts=raw_facts)


__all__ = [
    "annotate_chunks",
    "build_annotation_prompt",
    "chunk_offsets",
    "chunk_reference",
    "embedding_input",
]

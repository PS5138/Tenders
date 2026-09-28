"""Ingestion step 5: embed knowledge items.

``question_embedding`` is computed from the question text (pairs and promoted answers only)
and ``answer_embedding`` from the answer text (pairs) or from the heading-path-prefixed chunk
text (chunks). The prefixed string is an embedding-input concern only and is never stored.
Every text in a call to ``embed_items`` goes through one batched ``embed()`` call.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.enums import ItemType
from app.db.models import DocumentSection, KnowledgeItem
from app.llm.embeddings import embed

logger = logging.getLogger(__name__)

HEADING_PATH_SEPARATOR = " > "


def chunk_embedding_input(session: Session, item: KnowledgeItem) -> str:
    """The string embedded for a chunk: owner A's ``app.ingest.chunk.embedding_input`` when it
    exists, otherwise the section's heading path prepended to the raw slice."""
    section = session.get(DocumentSection, item.section_id)
    try:
        from app.ingest.chunk import embedding_input
    except ImportError:
        embedding_input = None
    if embedding_input is not None and section is not None:
        return str(embedding_input(item, section))
    heading = ""
    if section is not None and section.heading_path:
        heading = HEADING_PATH_SEPARATOR.join(str(part) for part in section.heading_path)
    return f"{heading}\n{item.answer_text}" if heading else item.answer_text


def embed_items(session: Session, items: Sequence[KnowledgeItem]) -> None:
    """Fill ``question_embedding`` and ``answer_embedding`` on ``items`` in one batched call.

    Flushes but does not commit. Items whose texts are empty keep null embeddings.
    """
    texts: list[str] = []
    targets: list[tuple[KnowledgeItem, str]] = []
    for item in items:
        if item.item_type == ItemType.CHUNK.value:
            answer_input = chunk_embedding_input(session, item)
        else:
            answer_input = item.answer_text or ""
            if item.question_text and item.question_text.strip():
                texts.append(item.question_text)
                targets.append((item, "question_embedding"))
        if answer_input.strip():
            texts.append(answer_input)
            targets.append((item, "answer_embedding"))
    if not texts:
        return
    vectors = embed(texts)
    for (item, attribute), vector in zip(targets, vectors, strict=True):
        setattr(item, attribute, vector)
    session.flush()
    logger.debug("embedded %d text(s) for %d item(s)", len(texts), len(items))

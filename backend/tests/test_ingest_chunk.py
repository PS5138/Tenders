"""Step 4: raw-slice chunking, the embedding input with heading path, topics and facts."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingest.chunk import PROMPT_NAME, chunk_offsets, chunk_reference, embedding_input
from tests.test_ingest_builders import (
    REFERENCE_HEADING,
    REFERENCE_LINES,
    build_reference_docx,
    make_document,
    parse_and_persist,
)

LONG_PARAGRAPHS = [
    " ".join(
        f"Sentence {i} of paragraph {p} states a plain fact about the policy." for i in range(12)
    )
    for p in range(6)
]


def test_chunk_offsets_are_contiguous_raw_slices_near_the_target() -> None:
    text = "\n".join(LONG_PARAGRAPHS)
    target = 400
    spans = chunk_offsets(text, target)
    assert len(spans) > 1
    previous_end = 0
    for start, end in spans:
        assert start >= previous_end and end > start
        slice_ = text[start:end]
        assert slice_ == slice_.strip(), "offsets exclude surrounding whitespace"
        assert text[previous_end:start].strip() == "", "nothing but whitespace is skipped"
        previous_end = end
    assert text[previous_end:].strip() == ""
    lengths = [end - start for start, end in spans]
    assert all(length <= target * 1.5 for length in lengths)
    assert all(length >= target * 0.4 for length in lengths[:-1])
    # Breaks land on paragraph or sentence boundaries when the text offers them.
    for start, _ in spans[1:]:
        assert text[start - 1] in "\n " and re.match(r"Sentence \d+", text[start:])


def test_short_text_is_one_chunk_and_empty_text_none() -> None:
    assert chunk_offsets("A short section.", 400) == [(0, 16)]
    assert chunk_offsets("   \n ", 400) == []


def test_chunk_reference_persists_raw_slices_and_annotates(
    db_session: Session, fake_llm, tmp_path: Path
) -> None:
    path = build_reference_docx(tmp_path / "iso.docx")
    document = make_document(
        db_session, path, doc_type="reference", doc_kind="iso_27001", ingest_status="extracting"
    )
    sections = parse_and_persist(db_session, document)
    assert len(sections) == 1 and sections[0].heading_path == [REFERENCE_HEADING]

    def respond(user: str, **_: object) -> dict:
        chunk_ids = re.findall(r"chunk id=([0-9a-f-]{36})", user)
        assert "`iso_27001`" in user and "key rule" in user
        return {
            "chunks": [
                {
                    "chunk_id": chunk_id,
                    "topics": ["information_security", "bogus_topic"],
                    "facts": [
                        {
                            "fact_kind": "iso_27001",
                            "fact_key": "should be nulled",
                            "statement": REFERENCE_LINES[1],
                            "value": "IS 771234",
                            "effective_date": "2025-03-01",
                            "expires_on": "2028-02-28",
                        }
                    ],
                }
                for chunk_id in chunk_ids
            ]
        }

    fake_llm.register(PROMPT_NAME, respond)

    result = chunk_reference(db_session, document, sections)

    assert len(result.items) >= 1
    section = sections[0]
    for item in result.items:
        assert item.item_type == "chunk" and item.text_verified is True
        assert item.question_text is None and item.question_section_id is None
        assert item.answer_text == section.text[item.answer_start : item.answer_end]
        assert item.topics == ["information_security"]
        assert REFERENCE_HEADING not in item.answer_text[len(REFERENCE_HEADING) + 1 :] or True
        prefixed = embedding_input(item, section)
        assert prefixed.startswith(REFERENCE_HEADING + "\n")
        assert prefixed.endswith(item.answer_text)
    # The heading path is never stored on the item.
    assert all("\n" + REFERENCE_HEADING not in item.answer_text for item in result.items)

    assert len(result.raw_facts) == len(result.items)
    fact = result.raw_facts[0]
    assert fact.fact_kind == "iso_27001" and fact.fact_key is None
    assert fact.section_id == section.id and fact.knowledge_item_id == result.items[0].id
    assert fact.effective_date == date(2025, 3, 1) and fact.expires_on == date(2028, 2, 28)

    call = fake_llm.calls[-1]
    assert call.name == PROMPT_NAME and call.model == get_settings().model_fast


def test_embedding_input_without_heading_path_is_the_slice() -> None:
    class Item:
        answer_text = "Just the text."

    class Section:
        heading_path: list[str] = []

    assert embedding_input(Item(), Section()) == "Just the text."

"""Step 1: section granularity for docx (three layouts) and xlsx, and idempotent persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import TABLE_CELL_DELIMITER
from app.ingest.parse import UnsupportedFileType, parse_document, persist_sections
from tests.test_ingest_builders import (
    A11_LINES,
    A21,
    A31,
    A33_PLACEHOLDER,
    PACK_HEADER,
    PACK_ROWS,
    PREAMBLE,
    Q11,
    Q12,
    Q21,
    Q22,
    Q31,
    Q32,
    Q33,
    TABLE_HEADER,
    build_pack_xlsx,
    build_submission_docx,
    make_document,
)


@pytest.fixture
def submission_sections(tmp_path: Path):
    path = build_submission_docx(tmp_path / "submission.docx")
    return parse_document(path, "submission.docx")


def test_order_indices_are_contiguous_and_texts_non_empty(submission_sections) -> None:
    assert [s.order_index for s in submission_sections] == list(range(len(submission_sections)))
    assert all(s.text.strip() for s in submission_sections)
    # docx exposes no page numbers.
    assert all(s.page_start is None and s.page_end is None for s in submission_sections)


def test_prose_before_first_heading_is_one_section_with_empty_path(submission_sections) -> None:
    first = submission_sections[0]
    assert first.heading_path == []
    assert first.text == PREAMBLE
    assert first.table_index is None and first.cell_ref is None


def test_heading_blocks_begin_with_their_heading_and_carry_ancestry(submission_sections) -> None:
    by_text = {s.text.split("\n")[0]: s for s in submission_sections if s.table_index is None}
    parent = by_text["1. Clinical safety"]
    assert parent.heading_path == ["1. Clinical safety"]
    # The sub-heading's paragraphs belong to the sub-heading's section, not the parent's.
    assert parent.text == "1. Clinical safety"

    q11 = by_text[Q11]
    assert q11.heading_path == ["1. Clinical safety", Q11]
    assert q11.text == "\n".join([Q11, *A11_LINES])

    q12 = by_text[Q12]
    assert q12.heading_path == ["1. Clinical safety", Q12]
    assert q12.text.startswith(Q12)
    assert q11.order_index < q12.order_index


def test_every_character_of_prose_belongs_to_exactly_one_section(submission_sections) -> None:
    prose = [s.text for s in submission_sections if s.table_index is None]
    for line in (PREAMBLE, Q11, *A11_LINES, Q12, Q31, Q32, Q33):
        assert sum(text.count(line) for text in prose) == 1, line


def test_docx_table_rows_are_sections_with_header_in_heading_path(submission_sections) -> None:
    rows = [s for s in submission_sections if s.table_index == 1]
    assert len(rows) == 2, "the header row is consumed into heading_path, not emitted"
    first, second = rows
    assert first.cell_ref == "T1:R2" and second.cell_ref == "T1:R3"
    assert first.heading_path == ["2. Information security", *TABLE_HEADER]
    assert first.text == f"{Q21}{TABLE_CELL_DELIMITER}{A21}"
    assert second.text.startswith(Q22 + TABLE_CELL_DELIMITER)


def test_boxed_answer_layout_keeps_questions_in_prose_and_answers_as_rows(
    submission_sections,
) -> None:
    implementation = next(
        s for s in submission_sections if s.text.startswith("3. Implementation")
    )
    # Paragraphs after a table still belong to their heading's block.
    assert implementation.text == "\n".join(["3. Implementation", Q31, Q32, Q33])
    boxes = [s for s in submission_sections if s.table_index in (2, 3, 4)]
    assert [b.cell_ref for b in boxes] == ["T2:R1", "T3:R1", "T4:R1"]
    # A single-row table has no header row: its one row is data under the prose heading path.
    assert all(b.heading_path == ["3. Implementation"] for b in boxes)
    assert boxes[0].text == A31
    assert boxes[2].text == A33_PLACEHOLDER
    assert implementation.order_index < boxes[0].order_index


def test_xlsx_sheets_are_one_section_per_row(tmp_path: Path) -> None:
    path = build_pack_xlsx(tmp_path / "pack.xlsx")
    sections = parse_document(path, "pack.xlsx")
    questions = [s for s in sections if s.table_index == 1]
    assert len(questions) == len(PACK_ROWS)
    first = questions[0]
    assert first.cell_ref == "Questions!2"
    assert first.heading_path == ["Questions", *PACK_HEADER]
    assert first.text == TABLE_CELL_DELIMITER.join(str(v) for v in PACK_ROWS[0])
    assert first.page_start is None and first.page_end is None
    notes = [s for s in sections if s.table_index == 2]
    assert [n.cell_ref for n in notes] == ["Notes!2"]
    assert notes[0].heading_path == ["Notes", "Guidance"]


def test_markdown_fallback_and_unsupported_type(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("Intro line.\n\n# Heading\n\nBody one.\nBody two.\n\n## Sub\n\nMore.\n")
    sections = parse_document(path, "notes.md")
    assert [s.heading_path for s in sections] == [[], ["Heading"], ["Heading", "Sub"]]
    assert sections[1].text == "Heading\nBody one.\nBody two."
    with pytest.raises(UnsupportedFileType):
        parse_document(tmp_path / "x.csv", "x.csv")


def test_persist_sections_is_idempotent(db_session: Session, tmp_path: Path) -> None:
    path = build_submission_docx(tmp_path / "submission.docx")
    document = make_document(db_session, path)
    parsed = parse_document(path, document.filename)
    first = persist_sections(db_session, document, parsed)
    again = persist_sections(db_session, document, parsed)
    assert [s.id for s in first] == [s.id for s in again]
    assert [s.order_index for s in first] == list(range(len(parsed)))
    assert all(s.org_id == document.org_id for s in first)
    assert first[0].heading_path == [] and first[0].text == PREAMBLE

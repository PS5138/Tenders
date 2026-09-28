"""Step 2: classification through the fake LLM, the upload-time date fallback and the sample."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingest.classify import PROMPT_NAME, classify_document, select_sample
from app.llm import prompt_version
from tests.test_ingest_builders import (
    build_reference_docx,
    build_submission_docx,
    make_document,
    parse_and_persist,
)


def test_classifies_past_submission_with_extracted_dates(
    db_session: Session, fake_llm, tmp_path: Path
) -> None:
    path = build_submission_docx(tmp_path / "submission.docx")
    document = make_document(db_session, path, ingest_status="classifying")
    sections = parse_and_persist(db_session, document)
    fake_llm.register(
        PROMPT_NAME,
        {
            "doc_type": "past_submission",
            "doc_kind": None,
            "effective_date": "2025-03-14",
            "buyer": "Barts Health NHS Trust",
            "submission_date": "2025-03-14",
            "rationale": "Buyer questions paired with supplier answers.",
        },
    )

    classify_document(db_session, document, sections)

    assert document.doc_type == "past_submission"
    assert document.doc_kind is None
    assert document.buyer == "Barts Health NHS Trust"
    assert document.submission_date == date(2025, 3, 14)
    assert document.effective_date == date(2025, 3, 14)
    assert document.effective_date_source == "extracted"
    assert document.classification_confirmed is False, "the pipeline never confirms"

    call = fake_llm.calls[-1]
    assert call.name == PROMPT_NAME
    assert call.model == get_settings().model_fast
    assert "Barts Health" in call.user, "the first sections are in the prompt"
    assert "`iso_27001`" in call.user, "the configured kinds are offered"
    assert "past_submission" in call.system
    assert prompt_version(PROMPT_NAME) == "classify_document.v1"


def test_reference_without_a_stated_date_falls_back_to_upload_time(
    db_session: Session, fake_llm, tmp_path: Path
) -> None:
    path = build_reference_docx(tmp_path / "iso.docx")
    uploaded = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)
    document = make_document(db_session, path, ingest_status="classifying", created_at=uploaded)
    sections = parse_and_persist(db_session, document)
    fake_llm.register(
        PROMPT_NAME,
        {"doc_type": "reference", "doc_kind": "iso_27001", "effective_date": None},
    )

    classify_document(db_session, document, sections)

    assert document.doc_type == "reference"
    assert document.doc_kind == "iso_27001"
    assert document.effective_date == date(2026, 9, 1)
    assert document.effective_date_source == "upload_time"
    assert document.buyer is None and document.submission_date is None


def test_unknown_kind_becomes_other_and_confirmed_documents_are_left_alone(
    db_session: Session, fake_llm, tmp_path: Path
) -> None:
    path = build_reference_docx(tmp_path / "policy.docx")
    document = make_document(db_session, path, ingest_status="classifying")
    sections = parse_and_persist(db_session, document)
    fake_llm.register(
        PROMPT_NAME,
        {"doc_type": "reference", "doc_kind": "not_a_kind", "effective_date": "2025-06-01"},
    )
    classify_document(db_session, document, sections)
    assert document.doc_kind == "other"

    # A user override already confirmed: a resumed classify stage must not overwrite it.
    document.doc_kind = "information_security_policy"
    document.classification_confirmed = True
    calls_before = len(fake_llm.calls)
    classify_document(db_session, document, sections)
    assert document.doc_kind == "information_security_policy"
    assert len(fake_llm.calls) == calls_before


def test_select_sample_takes_head_and_spread_of_the_rest() -> None:
    class S:
        def __init__(self, i: int) -> None:
            self.order_index = i

    sections = [S(i) for i in range(100)]
    sample = select_sample(sections)
    indices = [s.order_index for s in sample]
    assert indices[:12] == list(range(12))
    assert len(indices) == 20
    assert indices == sorted(indices) and len(set(indices)) == 20
    assert indices[-1] >= 85, "the sample reaches the end of the document"
    assert [s.order_index for s in select_sample(sections[:5])] == [0, 1, 2, 3, 4]

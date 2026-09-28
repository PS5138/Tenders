"""Ingestion step 7: fact persistence and the fact supersession gate."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.ingest.facts import evaluate_fact_supersession, persist_facts
from tests.test_supersession_factories import (  # noqa: F401 - fixture import
    InvalidationRecorder,
    events_for,
    invalidations_fixture,
    make_document,
    make_fact,
    make_item,
    make_section,
)


class _Raw:
    """Duck-typed stand-in for owner A's RawFact."""

    def __init__(self, **kwargs: object) -> None:
        self.fact_kind = kwargs.get("fact_kind", "iso_27001")
        self.fact_key = kwargs.get("fact_key")
        self.statement = kwargs.get("statement", "We hold ISO 27001 certificate IS 1.")
        self.value = kwargs.get("value", "IS 1")
        self.effective_date = kwargs.get("effective_date")
        self.expires_on = kwargs.get("expires_on")
        self.section_id = kwargs.get("section_id")
        self.knowledge_item_id = kwargs.get("knowledge_item_id")


def test_persist_facts_inherits_the_document_date_and_applies_key_rules(
    db_session: Session,
) -> None:
    document = make_document(db_session, effective_date=date(2025, 3, 1))
    section = make_section(db_session, document, "We hold ISO 27001 certificate IS 1.")
    item = make_item(db_session, document, section)
    facts = persist_facts(
        db_session,
        document,
        [
            _Raw(section_id=section.id, knowledge_item_id=item.id),
            _Raw(
                fact_kind="cyber_essentials_plus",
                fact_key="should be dropped",
                section_id=section.id,
                effective_date=date(2024, 12, 31),
                expires_on=date(2025, 12, 31),
            ),
            _Raw(
                fact_kind="insurance_cover",
                fact_key="  public   liability ",
                value="£10m",
                section_id=section.id,
            ),
            _Raw(
                fact_kind="insurance_cover",
                fact_key="cyber " * 60,
                value="£5m",
                section_id=section.id,
            ),
            _Raw(fact_kind="not_a_kind", section_id=section.id),
            _Raw(fact_kind="headcount", section_id=None, knowledge_item_id=None),
        ],
    )
    assert len(facts) == 4, "unknown kinds and facts without a section are skipped"
    iso, cyber, insurance, long_key = facts
    assert len("cyber " * 60) > 256
    assert len(long_key.fact_key) <= 256, "a key over the column length is cut, not fatal"
    assert long_key.fact_key.startswith("cyber cyber") and not long_key.fact_key.endswith(" ")
    assert iso.effective_date == date(2025, 3, 1), "null date inherited from the document"
    assert iso.knowledge_item_id == item.id
    assert iso.fact_key is None
    assert cyber.effective_date == date(2024, 12, 31) and cyber.expires_on == date(2025, 12, 31)
    assert cyber.fact_key is None, "unkeyed kinds always store a null key"
    assert insurance.fact_key == "public liability", "keys are whitespace-collapsed"
    assert insurance.value == "£10m"


def _pair(
    session: Session,
    *,
    older_kwargs: dict | None = None,
    newer_kwargs: dict | None = None,
    fact_kind: str = "iso_27001",
    older_key: str | None = None,
    newer_key: str | None = None,
    older_date: date = date(2024, 1, 1),
    newer_date: date = date(2025, 1, 1),
):
    older_doc = make_document(session, effective_date=older_date, **(older_kwargs or {}))
    newer_doc = make_document(session, effective_date=newer_date, **(newer_kwargs or {}))
    older = make_fact(session, older_doc, fact_kind=fact_kind, fact_key=older_key)
    newer = make_fact(session, newer_doc, fact_kind=fact_kind, fact_key=newer_key)
    return older_doc, newer_doc, older, newer


def test_later_dated_fact_supersedes_regardless_of_upload_order(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    # The newer-dated document is created first: upload order must not matter.
    newer_doc = make_document(db_session, effective_date=date(2025, 1, 1))
    older_doc = make_document(db_session, effective_date=date(2024, 1, 1))
    newer = make_fact(db_session, newer_doc)
    older = make_fact(db_session, older_doc)

    evaluate_fact_supersession(db_session, older_doc)

    assert older.superseded_by == newer.id
    assert newer.superseded_by is None
    assert invalidations.calls == [("fact", older.id, "superseded")]
    events = events_for(db_session, older.id, "fact_superseded")
    assert len(events) == 1
    assert events[0].actor == "system"
    assert events[0].payload["superseded_by"] == str(newer.id)

    # Re-evaluation from either side is idempotent.
    evaluate_fact_supersession(db_session, newer_doc)
    evaluate_fact_supersession(db_session, older_doc)
    assert older.superseded_by == newer.id
    assert len(events_for(db_session, older.id, "fact_superseded")) == 1
    assert len(invalidations.calls) == 1


@pytest.mark.parametrize(
    ("older_kwargs", "newer_kwargs", "newer_date", "why"),
    [
        ({"confirmed": False}, {}, date(2025, 1, 1), "the older document is unconfirmed"),
        ({}, {"confirmed": False}, date(2025, 1, 1), "the newer document is unconfirmed"),
        ({"source": "upload_time"}, {}, date(2025, 1, 1), "the older date is upload time"),
        ({}, {"source": "upload_time"}, date(2025, 1, 1), "the newer date is upload time"),
        ({}, {}, date(2024, 1, 1), "the dates tie"),
        ({"doc_type": "past_submission"}, {}, date(2025, 1, 1), "doc_types differ"),
    ],
)
def test_gate_blocks(
    db_session: Session,
    invalidations: InvalidationRecorder,
    older_kwargs: dict,
    newer_kwargs: dict,
    newer_date: date,
    why: str,
) -> None:
    older_doc, newer_doc, older, newer = _pair(
        db_session, older_kwargs=older_kwargs, newer_kwargs=newer_kwargs, newer_date=newer_date
    )
    evaluate_fact_supersession(db_session, older_doc)
    evaluate_fact_supersession(db_session, newer_doc)
    assert older.superseded_by is None and newer.superseded_by is None, why
    assert invalidations.calls == []
    assert events_for(db_session, older.id) == []


def test_gate_applies_to_past_submissions_of_any_kind(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older_doc, newer_doc, older, newer = _pair(
        db_session,
        older_kwargs={"doc_type": "past_submission"},
        newer_kwargs={"doc_type": "past_submission"},
        fact_kind="headcount",
    )
    evaluate_fact_supersession(db_session, newer_doc)
    assert older.superseded_by == newer.id
    assert invalidations.of("fact", "superseded") == [older.id]


def test_keyed_kinds_supersede_only_within_the_same_key(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older_doc, newer_doc, older, newer = _pair(
        db_session,
        older_kwargs={"doc_kind": "dspt_confirmation"},
        newer_kwargs={"doc_kind": "dspt_confirmation"},
        fact_kind="dspt_status",
        older_key="X26",
        newer_key="Y99",
    )
    evaluate_fact_supersession(db_session, newer_doc)
    assert older.superseded_by is None, "different legal entities are both current"

    same_key = make_fact(db_session, newer_doc, fact_kind="dspt_status", fact_key="x26")
    evaluate_fact_supersession(db_session, newer_doc)
    assert older.superseded_by == same_key.id, "keys compare after normalisation"
    assert invalidations.of("fact", "superseded") == [older.id]


def test_re_evaluation_clears_a_pointer_the_rule_no_longer_supports(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older_doc, newer_doc, older, newer = _pair(db_session)
    evaluate_fact_supersession(db_session, older_doc)
    assert older.superseded_by == newer.id

    # The newer document's date turns out to be the upload time: the gate is no longer met.
    newer_doc.effective_date_source = "upload_time"
    evaluate_fact_supersession(db_session, newer_doc)
    assert older.superseded_by is None
    # Clearing restores nothing and writes nothing new.
    assert invalidations.calls == [("fact", older.id, "superseded")]
    assert len(events_for(db_session, older.id, "fact_superseded")) == 1


def test_the_latest_of_several_successors_is_chosen(db_session: Session) -> None:
    oldest_doc = make_document(db_session, effective_date=date(2023, 1, 1))
    middle_doc = make_document(db_session, effective_date=date(2024, 1, 1))
    latest_doc = make_document(db_session, effective_date=date(2025, 1, 1))
    oldest = make_fact(db_session, oldest_doc)
    middle = make_fact(db_session, middle_doc)
    latest = make_fact(db_session, latest_doc)
    evaluate_fact_supersession(db_session, middle_doc)
    assert oldest.superseded_by == latest.id
    assert middle.superseded_by == latest.id
    assert latest.superseded_by is None


def test_missing_invalidation_does_not_break_ingestion(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.review import invalidation

    def not_ready(*_: object, **__: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(invalidation, "invalidate", not_ready)
    older_doc, newer_doc, older, newer = _pair(db_session)
    evaluate_fact_supersession(db_session, older_doc)
    assert older.superseded_by == newer.id
    assert isinstance(older.id, uuid.UUID)

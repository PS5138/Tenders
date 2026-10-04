"""Ingestion step 9: corrections (PATCH overrides, confirmation and stage re-runs)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentSection,
    Fact,
    Job,
    KnowledgeItem,
    SupersessionDecision,
    UnpairedFragment,
)
from app.ingest.corrections import PatchError, apply_patch, confirm_document, rerun_from_stage
from app.ingest.dedup import deduplicate
from app.ingest.supersession import apply_decision, evaluate_document_supersession
from tests.test_supersession_factories import (  # noqa: F401 - fixture import
    ORG,
    InvalidationRecorder,
    events_for,
    invalidations_fixture,
    make_document,
    make_fact,
    make_fragment,
    make_item,
    make_section,
)

ANSWER = "We hold ISO 27001 certification covering the platform and its hosting."


def test_rerun_from_extracting_removes_outputs_but_never_sections(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older = make_document(db_session, effective_date=date(2024, 1, 1))
    newer = make_document(db_session, effective_date=date(2025, 1, 1))
    other_doc = make_document(
        db_session, doc_type="past_submission", effective_date=date(2024, 6, 1)
    )

    section = make_section(db_session, newer, ANSWER)
    item = make_item(db_session, newer, section, answer_text=ANSWER, canonical=False)
    other_item = make_item(db_session, other_doc, answer_text=ANSWER, canonical=False)
    deduplicate(db_session, ORG, [other_item, item])
    assert item.is_canonical and other_item.canonical_id == item.id, "newest date is canonical"

    fact = make_fact(db_session, newer, section, item=item)
    older_fact = make_fact(db_session, older)
    older_fact.superseded_by = fact.id
    fragment = make_fragment(db_session, newer, section)
    evaluate_document_supersession(db_session, newer)
    assert older.superseded_by == newer.id
    older_item = make_item(db_session, older)
    older_item.excluded_from_retrieval = True
    db_session.flush()
    invalidations.calls.clear()

    rerun_from_stage(db_session, newer, "extracting")

    assert db_session.get(KnowledgeItem, item.id) is None
    assert db_session.get(Fact, fact.id) is None
    assert db_session.get(UnpairedFragment, fragment.id) is None
    assert db_session.get(DocumentSection, section.id) is not None, "sections are never deleted"
    # The fact supersession this document's fact set is cleared, restoring nothing.
    db_session.refresh(older_fact)
    assert older_fact.superseded_by is None
    # Decision rows are kept for the linking stage to reconcile, so the partner stays
    # superseded and its items stay excluded across the re-run.
    rows = db_session.scalars(select(SupersessionDecision)).all()
    assert len(rows) == 1 and rows[0].decision == "superseded"
    db_session.refresh(older)
    db_session.refresh(older_item)
    assert older.superseded_by == newer.id and older_item.excluded_from_retrieval is True
    # The cluster that lost its canonical re-elected the survivor.
    db_session.refresh(other_item)
    assert other_item.is_canonical and other_item.canonical_id is None
    # Deleted items (and their facts) went through invalidation with reason removed.
    assert invalidations.of("knowledge_item", "removed") == [item.id]
    assert invalidations.of("fact", "removed") == [fact.id]


def test_rerun_from_linking_keeps_items_and_facts(db_session: Session) -> None:
    older = make_document(db_session, effective_date=date(2024, 1, 1))
    newer = make_document(db_session, effective_date=date(2025, 1, 1))
    item = make_item(db_session, newer)
    fact = make_fact(db_session, newer)
    evaluate_document_supersession(db_session, newer)
    assert db_session.scalars(select(SupersessionDecision)).all()

    rerun_from_stage(db_session, newer, "linking")

    assert db_session.get(KnowledgeItem, item.id) is not None
    assert db_session.get(Fact, fact.id) is not None
    rows = db_session.scalars(select(SupersessionDecision)).all()
    assert len(rows) == 1 and rows[0].decision == "superseded"
    assert older.superseded_by == newer.id

    with pytest.raises(ValueError):
        rerun_from_stage(db_session, newer, "ready")


@pytest.mark.parametrize("stage", ["extracting", "embedding", "linking"])
def test_rerun_keeps_a_human_keep_both(db_session: Session, stage: str) -> None:
    older = make_document(db_session, effective_date=date(2024, 1, 1))
    newer = make_document(db_session, effective_date=date(2025, 1, 1))
    older_item = make_item(db_session, older)
    evaluate_document_supersession(db_session, newer)
    row = db_session.scalars(select(SupersessionDecision)).one()
    apply_decision(db_session, row, "keep_both", None, "bid lead")
    assert older.superseded_by is None
    events_before = len(events_for(db_session, older.id))

    rerun_from_stage(db_session, newer, stage)
    # The linking stage of the resumed job.
    evaluate_document_supersession(db_session, newer)

    db_session.refresh(row)
    db_session.refresh(older)
    db_session.refresh(older_item)
    assert row.decision == "keep_both" and row.decided_by == "bid lead"
    assert older.superseded_by is None and older_item.excluded_from_retrieval is False
    assert len(events_for(db_session, older.id)) == events_before, "a retry writes no events"


def test_extracting_rerun_of_a_human_superseded_document_excludes_its_new_items(
    db_session: Session,
) -> None:
    older = make_document(db_session, effective_date=date(2024, 1, 1))
    newer = make_document(db_session, effective_date=date(2025, 1, 1))
    evaluate_document_supersession(db_session, newer)
    row = db_session.scalars(select(SupersessionDecision)).one()
    # A person overrides the automatic outcome: the 2025 document is the superseded one.
    apply_decision(db_session, row, "superseded", older.id, "ig lead")
    assert newer.superseded_by == older.id

    rerun_from_stage(db_session, newer, "extracting")
    recreated = make_item(db_session, newer)
    assert recreated.excluded_from_retrieval is False
    evaluate_document_supersession(db_session, newer)

    db_session.refresh(row)
    db_session.refresh(recreated)
    assert row.decided_by == "ig lead" and row.superseding_document_id == older.id
    assert newer.superseded_by == older.id
    assert recreated.excluded_from_retrieval is True


def test_patch_doc_type_change_enqueues_a_re_ingest_from_extracting(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    document = make_document(db_session, confirmed=False, source="upload_time")
    item = make_item(db_session, document)
    make_fact(db_session, document)

    job = apply_patch(db_session, document, {"doc_type": "past_submission"}, "bid lead")

    assert isinstance(job, Job)
    assert job.kind == "ingest_document" and job.status == "queued"
    assert job.payload == {"document_id": str(document.id), "actor": "bid lead"}
    assert job.total == 5
    assert document.ingest_status == "extracting"
    assert document.classification_confirmed is True
    assert document.doc_type == "past_submission" and document.doc_kind is None
    assert document.effective_date_source == "upload_time", "the date did not change"
    # The delete-then-re-run belongs to the worker's resume rule, not to the request.
    assert db_session.get(KnowledgeItem, item.id) is not None, "outputs survive until the job runs"
    assert invalidations.calls == []


def test_patch_doc_type_change_re_evaluates_the_former_kind_and_reverses_exclusion(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    # Confirmed in the order A, B, C: the rows are (A, B) and (B, C); C is never paired with
    # the already-superseded A.
    oldest = make_document(db_session, effective_date=date(2023, 1, 1))
    newest = make_document(db_session, effective_date=date(2025, 1, 1))
    middle = make_document(db_session, effective_date=date(2024, 1, 1))
    oldest_item = make_item(db_session, oldest)
    middle_item = make_item(db_session, middle)
    for document in (oldest, newest, middle):
        evaluate_document_supersession(db_session, document)
    assert oldest.superseded_by == newest.id and middle.superseded_by == newest.id
    rows_before = db_session.scalars(select(SupersessionDecision)).all()
    assert {frozenset((r.document_a_id, r.document_b_id)) for r in rows_before} == {
        frozenset((oldest.id, newest.id)),
        frozenset((middle.id, newest.id)),
    }

    job = apply_patch(db_session, newest, {"doc_type": "past_submission"}, "bid lead")

    assert isinstance(job, Job) and newest.ingest_status == "extracting"
    rows = db_session.scalars(select(SupersessionDecision)).all()
    assert len(rows) == 1, "one row per pair of confirmed reference documents of the kind"
    row = rows[0]
    assert row.doc_kind == "iso_27001"
    assert {row.document_a_id, row.document_b_id} == {oldest.id, middle.id}
    assert row.decision == "superseded" and row.superseding_document_id == middle.id
    assert oldest.superseded_by == middle.id, "the restored partners were re-evaluated"
    assert middle.superseded_by is None and newest.superseded_by is None
    db_session.refresh(oldest_item)
    db_session.refresh(middle_item)
    assert oldest_item.excluded_from_retrieval is True
    assert middle_item.excluded_from_retrieval is False


def test_patch_to_reference_writes_no_rows_before_the_re_ingest(db_session: Session) -> None:
    existing = make_document(db_session, effective_date=date(2024, 1, 1))
    document = make_document(
        db_session, doc_type="past_submission", effective_date=date(2025, 1, 1)
    )

    job = apply_patch(
        db_session, document, {"doc_type": "reference", "doc_kind": "iso_27001"}, "bid lead"
    )

    assert isinstance(job, Job)
    assert document.doc_type == "reference" and document.doc_kind == "iso_27001"
    assert db_session.scalars(select(SupersessionDecision)).all() == []
    assert existing.superseded_by is None, "step 8 runs in the worker's linking stage"


def test_patch_date_change_records_user_source_and_re_evaluates_supersession(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older = make_document(db_session, effective_date=date(2024, 1, 1))
    newer = make_document(db_session, effective_date=date(2025, 1, 1), confirmed=False)
    older_item = make_item(db_session, older)

    job = apply_patch(db_session, newer, {"effective_date": "2025-02-01"}, "bid lead")

    assert job is None
    assert newer.classification_confirmed is True
    assert newer.effective_date == date(2025, 2, 1)
    assert newer.effective_date_source == "user"
    assert older.superseded_by == newer.id
    db_session.refresh(older_item)
    assert older_item.excluded_from_retrieval is True

    # Correcting the older document to a later date flips the pair.
    apply_patch(db_session, older, {"effective_date": date(2026, 1, 1)}, "bid lead")
    assert older.superseded_by is None and newer.superseded_by == older.id
    db_session.refresh(older_item)
    assert older_item.excluded_from_retrieval is False


def test_patch_date_change_moves_inherited_fact_dates_before_the_gate_runs(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    from app.retrieve.facts import currency_sort_key

    dated = make_document(
        db_session, doc_type="past_submission", effective_date=date(2024, 1, 1)
    )
    # Classified with no date: dated at upload time, and its undated fact inherits that date.
    undated = make_document(
        db_session,
        doc_type="past_submission",
        effective_date=date(2026, 9, 28),
        source="upload_time",
        confirmed=False,
    )
    dated_fact = make_fact(db_session, dated, fact_kind="dspt_status", fact_key="X26")
    inherited = make_fact(db_session, undated, fact_kind="dspt_status", fact_key="X26")
    stated = make_fact(
        db_session, undated, fact_kind="headcount", effective_date=date(2025, 5, 5)
    )
    assert inherited.effective_date == date(2026, 9, 28)

    job = apply_patch(db_session, undated, {"effective_date": "2023-06-30"}, "bid lead")

    assert job is None
    db_session.refresh(inherited)
    db_session.refresh(stated)
    assert inherited.effective_date == date(2023, 6, 30), "the inherited date follows the override"
    assert stated.effective_date == date(2025, 5, 5), "a date the text states is left alone"
    # The gate now opens on the corrected dates: the older document's fact is the superseded one.
    assert inherited.superseded_by == dated_fact.id
    assert dated_fact.superseded_by is None
    assert invalidations.of("fact", "superseded") == [inherited.id]
    assert len(events_for(db_session, inherited.id, "fact_superseded")) == 1
    assert events_for(db_session, dated_fact.id) == []
    # And the currency ranking treats it as a 2023 fact, behind the genuinely newer one.
    assert currency_sort_key(dated_fact, dated) < currency_sort_key(inherited, undated)


def test_patch_kind_change_re_evaluates_the_former_kind_and_reverses_exclusion(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older = make_document(db_session, effective_date=date(2024, 1, 1))
    middle = make_document(db_session, effective_date=date(2024, 6, 1))
    newer = make_document(db_session, effective_date=date(2025, 1, 1))
    older_item = make_item(db_session, older)
    middle_item = make_item(db_session, middle)
    evaluate_document_supersession(db_session, middle)
    evaluate_document_supersession(db_session, newer)
    # A superseded document is not paired with newcomers: the chain is older -> middle -> newer.
    assert older.superseded_by == middle.id and middle.superseded_by == newer.id
    rows_before = db_session.scalars(select(SupersessionDecision)).all()
    assert len(rows_before) == 2
    db_session.refresh(middle_item)
    assert middle_item.excluded_from_retrieval is True

    apply_patch(db_session, newer, {"doc_kind": "information_security_policy"}, "bid lead")

    assert newer.doc_kind == "information_security_policy"
    rows = db_session.scalars(select(SupersessionDecision)).all()
    assert {row.doc_kind for row in rows} == {"iso_27001"}
    assert len(rows) == 1, "the (middle, newer) row of the former kind is gone"
    assert middle.superseded_by is None, "middle's exclusion is reversed"
    db_session.refresh(middle_item)
    assert middle_item.excluded_from_retrieval is False
    assert older.superseded_by == middle.id, "the former kind still supersedes within itself"
    db_session.refresh(older_item)
    assert older_item.excluded_from_retrieval is True


def test_patch_of_a_non_gate_field_only_confirms(db_session: Session) -> None:
    document = make_document(
        db_session, doc_type="past_submission", confirmed=False, effective_date=date(2024, 1, 1)
    )
    job = apply_patch(db_session, document, {"buyer": " NHS Somewhere "}, "bid lead")
    assert job is None
    assert document.buyer == "NHS Somewhere"
    assert document.classification_confirmed is True
    assert document.effective_date_source == "extracted"


@pytest.mark.parametrize(
    "changes",
    [
        {"doc_type": "tender_document"},
        {"doc_kind": "not_a_kind"},
        {"effective_date": "yesterday"},
        {"effective_date": None},
        {"filename": "x"},
    ],
)
def test_patch_rejects_invalid_values(db_session: Session, changes: dict) -> None:
    document = make_document(db_session)
    with pytest.raises(PatchError):
        apply_patch(db_session, document, changes, "bid lead")


def test_patch_requires_a_kind_on_a_reference_document(db_session: Session) -> None:
    submission = make_document(db_session, doc_type="past_submission", confirmed=False)
    with pytest.raises(PatchError, match="doc_kind"):
        apply_patch(db_session, submission, {"doc_type": "reference"}, "bid lead")
    assert submission.doc_type == "past_submission", "nothing was applied"
    assert submission.classification_confirmed is False

    reference = make_document(db_session)
    with pytest.raises(PatchError, match="doc_kind"):
        apply_patch(db_session, reference, {"doc_kind": None}, "bid lead")
    assert reference.doc_kind == "iso_27001"

    # Supplying both fields keeps working, and a kind alone is still a plain override.
    job = apply_patch(
        db_session, submission, {"doc_type": "reference", "doc_kind": "iso_27001"}, "bid lead"
    )
    assert isinstance(job, Job) and submission.doc_kind == "iso_27001"
    assert apply_patch(db_session, reference, {"doc_kind": "cyber_essentials_plus"}, "x") is None


def test_confirm_document_sets_the_flag_and_evaluates_both_rules(
    db_session: Session, invalidations: InvalidationRecorder
) -> None:
    older = make_document(db_session, effective_date=date(2024, 1, 1))
    newer = make_document(db_session, effective_date=date(2025, 1, 1), confirmed=False)
    older_fact = make_fact(db_session, older)
    newer_fact = make_fact(db_session, newer)
    assert db_session.scalars(select(SupersessionDecision)).all() == []

    confirm_document(db_session, newer, "bid lead")

    assert newer.classification_confirmed is True
    assert older_fact.superseded_by == newer_fact.id
    assert older.superseded_by == newer.id
    rows = db_session.scalars(select(SupersessionDecision)).all()
    assert len(rows) == 1 and rows[0].decision == "superseded"

"""Fact selection (draft step 5): attached facts, currency and the preference rules."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID
from app.retrieve.facts import fact_kinds_for_topics, select_facts
from tests.test_retrieve_helpers import make_document, make_fact, make_item

ORG = DEFAULT_ORG_ID
TODAY = date(2026, 9, 28)


def test_fact_kinds_for_topics_follows_the_configured_mapping() -> None:
    assert fact_kinds_for_topics(["information_security"]) == [
        "dspt_status",
        "cyber_essentials_plus",
        "iso_27001",
    ]
    assert fact_kinds_for_topics(["clinical_safety", "information_governance"]) == [
        "clinical_safety_case",
        "clinical_safety_officer",
        "mhra_registration",
        "data_protection_officer",
    ]
    assert fact_kinds_for_topics([]) == []
    assert fact_kinds_for_topics(["social_value"]) == []


def test_attached_facts_come_first_in_candidate_order_and_free_text_gets_only_those(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session)
    first = make_item(db_session, doc, answer_text="Our DPO is Jane Doe.")
    second = make_item(db_session, doc, answer_text="Headcount is 42.")
    unretrieved = make_item(db_session, doc, answer_text="ISO 27001 held.")
    dpo = make_fact(
        db_session, doc, fact_kind="data_protection_officer", knowledge_item=first,
        statement="Our DPO is Jane Doe.", value="Jane Doe",
    )
    headcount = make_fact(
        db_session, doc, fact_kind="headcount", knowledge_item=second, statement="Headcount 42.",
        value="42",
    )
    superseded_but_attached = make_fact(
        db_session, doc, fact_kind="headcount", knowledge_item=second, statement="Headcount 40.",
        value="40", effective_date=date(2024, 1, 1), superseded_by=headcount.id,
    )
    make_fact(db_session, doc, fact_kind="iso_27001", knowledge_item=unretrieved)
    # A current topic-mapped fact that is not attached: excluded for free text.
    make_fact(db_session, doc, fact_kind="cyber_essentials_plus", statement="CE+ held.")

    selected = select_facts(db_session, ORG, [second, first], topics=None, today=TODAY)
    assert [fact.id for fact in selected] == [headcount.id, superseded_but_attached.id, dpo.id]

    with_topics = select_facts(
        db_session, ORG, [second, first], topics=["information_security"], today=TODAY
    )
    kinds = [fact.fact_kind for fact in with_topics]
    assert kinds[:3] == ["headcount", "headcount", "data_protection_officer"]
    assert "cyber_essentials_plus" in kinds and "iso_27001" in kinds
    assert "dspt_status" not in kinds, "no current fact of that kind exists"


def test_currency_excludes_superseded_expired_swept_and_superseded_document_facts(
    db_session: Session, fake_embeddings
) -> None:
    current_doc = make_document(db_session, doc_type="reference", doc_kind="iso_27001")
    old_doc = make_document(db_session, doc_type="reference", doc_kind="iso_27001",
                            superseded_by=current_doc.id, effective_date=date(2024, 1, 1))

    live = make_fact(db_session, current_doc, fact_kind="iso_27001", statement="live",
                     expires_on=TODAY)
    superseded = make_fact(db_session, current_doc, fact_kind="cyber_essentials_plus",
                           statement="old", superseded_by=live.id)
    expired = make_fact(db_session, current_doc, fact_kind="cyber_essentials_plus",
                        statement="expired", expires_on=TODAY - timedelta(days=1))
    swept = make_fact(db_session, current_doc, fact_kind="cyber_essentials_plus",
                      statement="swept", expired_at=datetime.now(UTC))
    from_superseded_doc = make_fact(db_session, old_doc, fact_kind="dspt_status",
                                    fact_key="X26", statement="stale document")

    selected = select_facts(db_session, ORG, [], topics=["information_security"], today=TODAY)
    ids = {fact.id for fact in selected}
    assert live.id in ids, "expires_on equal to today is still current"
    assert not ids & {superseded.id, expired.id, swept.id, from_superseded_doc.id}


def test_one_fact_per_kind_and_key_with_the_preference_rules(
    db_session: Session, fake_embeddings
) -> None:
    reference = make_document(db_session, doc_type="reference", doc_kind="dspt_confirmation",
                              effective_date=date(2024, 6, 1))
    submission = make_document(db_session, doc_type="past_submission",
                               effective_date=date(2025, 6, 1))
    uploaded = make_document(db_session, doc_type="past_submission",
                             effective_date=date(2026, 9, 1),
                             effective_date_source="upload_time")

    # dspt_status, key X26: an older reference fact beats a newer past-submission fact.
    ref_x26 = make_fact(db_session, reference, fact_kind="dspt_status", fact_key="X26",
                        statement="ref", effective_date=date(2024, 6, 1))
    make_fact(db_session, submission, fact_kind="dspt_status", fact_key="X26",
              statement="sub", effective_date=date(2025, 6, 1))
    # dspt_status, key Y99: only past submissions; newest stated date wins over an
    # upload-time-inherited date that is even newer.
    sub_y99_new = make_fact(db_session, submission, fact_kind="dspt_status", fact_key="Y99",
                            statement="new", effective_date=date(2025, 6, 1))
    make_fact(db_session, submission, fact_kind="dspt_status", fact_key="Y99",
              statement="older", effective_date=date(2023, 6, 1))
    make_fact(db_session, uploaded, fact_kind="dspt_status", fact_key="Y99",
              statement="inherited", effective_date=date(2026, 9, 1))
    # iso_27001, null key: the only candidates are upload-time inherited and a stated older date
    # in the same upload-time document; the stated one is preferred.
    make_fact(db_session, uploaded, fact_kind="iso_27001", statement="inherited",
              effective_date=date(2026, 9, 1))
    iso_stated = make_fact(db_session, uploaded, fact_kind="iso_27001", statement="stated",
                           effective_date=date(2025, 3, 1))

    selected = select_facts(db_session, ORG, [], topics=["information_security"], today=TODAY)
    by_key = {(fact.fact_kind, fact.fact_key): fact for fact in selected}
    assert len(selected) == len(by_key) == 3, "exactly one fact per (kind, key)"
    assert by_key[("dspt_status", "X26")].id == ref_x26.id
    assert by_key[("dspt_status", "Y99")].id == sub_y99_new.id
    assert by_key[("iso_27001", None)].id == iso_stated.id
    assert [fact.fact_kind for fact in selected] == ["dspt_status", "dspt_status", "iso_27001"]


def test_topic_fact_that_is_also_attached_appears_once(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session, doc_type="reference", doc_kind="clinical_safety_case")
    item = make_item(db_session, doc, answer_text="Our CSO is Dr Ada Lovelace.")
    cso = make_fact(db_session, doc, fact_kind="clinical_safety_officer", knowledge_item=item,
                    statement="Our CSO is Dr Ada Lovelace.", value="Dr Ada Lovelace")
    selected = select_facts(db_session, ORG, [item], topics=["clinical_safety"], today=TODAY)
    assert [fact.id for fact in selected] == [cso.id]

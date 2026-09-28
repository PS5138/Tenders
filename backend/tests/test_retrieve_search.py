"""The three ranked lists, fusion over real rows, collapse, best_vec and the query vector."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID, get_settings
from app.llm import embed
from app.retrieve.query import get_query_vector
from app.retrieve.search import (
    build_lexical_query,
    lexical_list,
    retrieve,
    topic_list,
    vector_list,
)
from tests.test_retrieve_helpers import make_document, make_item, make_question, unit

ORG = DEFAULT_ORG_ID
N = 20


def test_build_lexical_query_drops_stopwords_and_or_joins() -> None:
    query = build_lexical_query(
        "Please describe your approach to DCB0129 clinical safety; describe the ISO 27001 scope."
    )
    assert query == "your or to or dcb0129 or clinical or safety or the or iso or 27001 or scope"
    assert build_lexical_query("Please describe your approach") == "your"
    assert build_lexical_query("Please describe") == ""
    assert build_lexical_query("") == ""


def test_lexical_list_orders_by_ts_rank_and_respects_eligibility(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session)
    in_question = make_item(
        db_session,
        doc,
        question_text="How do you meet DCB0129?",
        answer_text="We maintain a clinical safety case and hazard log for every release.",
    )
    in_answer = make_item(
        db_session,
        doc,
        question_text="Describe your release process.",
        answer_text="Releases are reviewed against DCB0129 by the clinical safety officer.",
    )
    unrelated = make_item(
        db_session, doc, question_text="Describe social value.", answer_text="We hire locally."
    )
    make_item(
        db_session,
        doc,
        question_text="DCB0129 unverified",
        answer_text="DCB0129 DCB0129 DCB0129 unverified copy.",
        text_verified=False,
    )
    make_item(
        db_session,
        doc,
        question_text="DCB0129 excluded",
        answer_text="DCB0129 DCB0129 superseded document.",
        excluded=True,
    )

    ranked = lexical_list(db_session, ORG, build_lexical_query("DCB0129"), N)
    assert ranked == [in_question.id, in_answer.id], "question text carries weight A"
    assert unrelated.id not in ranked

    both = lexical_list(db_session, ORG, build_lexical_query("release DCB0129"), N)
    assert set(both) == {in_question.id, in_answer.id}
    assert both[0] == in_answer.id, "the item matching both OR terms ranks first"

    # Only Postgres stopwords survive the Python filter: the tsquery is empty, the list too.
    assert lexical_list(db_session, ORG, build_lexical_query("the and of"), N) == []
    assert lexical_list(db_session, ORG, "", N) == []


def test_vector_list_scores_the_better_of_the_two_embeddings(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session)
    query = unit(1.0)
    by_answer = make_item(
        db_session, doc, answer_text="answer wins", answer_embedding=unit(1.0),
        question_embedding=None,
    )
    by_question = make_item(
        db_session,
        doc,
        question_text="question wins",
        answer_text="answer is far",
        answer_embedding=unit(0.2, 0.98),
        question_embedding=unit(0.9, 0.436),
    )
    chunk = make_item(
        db_session, doc, answer_text="half way", answer_embedding=unit(0.5, 0.866),
        question_embedding=None,
    )
    make_item(db_session, doc, answer_text="no embedding", answer_embedding=None,
              question_embedding=None)
    make_item(db_session, doc, answer_text="ineligible", answer_embedding=unit(1.0),
              question_embedding=None, text_verified=False)

    ranked = vector_list(db_session, ORG, query, N)
    ids = [item_id for item_id, _ in ranked]
    scores = dict(ranked)
    assert ids == [by_answer.id, by_question.id, chunk.id]
    assert scores[by_answer.id] == pytest.approx(1.0, abs=1e-6)
    assert scores[by_question.id] == pytest.approx(0.9, abs=1e-3)
    assert scores[chunk.id] == pytest.approx(0.5, abs=1e-3)


def test_topic_list_filters_by_intersection_and_ranks_by_vector_score(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session)
    query = unit(1.0)
    far_but_on_topic = make_item(
        db_session, doc, answer_text="far", topics=["clinical_safety"],
        answer_embedding=unit(0.3, 0.954), question_embedding=None,
    )
    near_and_on_topic = make_item(
        db_session, doc, answer_text="near", topics=["information_governance", "clinical_safety"],
        answer_embedding=unit(0.95, 0.312), question_embedding=None,
    )
    make_item(
        db_session, doc, answer_text="off topic", topics=["social_value"],
        answer_embedding=unit(1.0), question_embedding=None,
    )
    ranked = topic_list(db_session, ORG, query, ["clinical_safety", "interoperability"], N)
    assert [item_id for item_id, _ in ranked] == [near_and_on_topic.id, far_but_on_topic.id]
    assert topic_list(db_session, ORG, query, [], N) == []


def test_retrieve_fuses_the_lists_present_and_reports_ranks(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session)
    k = get_settings().rrf_k
    # A: the lexical hit (last by vector). B: vector top, topic top. C: second in both.
    a = make_item(
        db_session, doc, question_text="ISO 27001 certificate number",
        answer_text="Our ISO 27001 certificate number is IS 12345.",
        answer_embedding=unit(0.0, 1.0), question_embedding=None, topics=["social_value"],
    )
    b = make_item(
        db_session, doc, answer_text="security controls", topics=["information_security"],
        answer_embedding=unit(0.99, 0.141), question_embedding=None,
    )
    c = make_item(
        db_session, doc, answer_text="governance controls", topics=["information_security"],
        answer_embedding=unit(0.8, 0.6), question_embedding=None,
    )

    result = retrieve(
        db_session, ORG, query_text="ISO 27001", query_vector=unit(1.0),
        topics=["information_security"],
    )
    assert result.lexical_query == "iso or 27001"
    assert result.lists["lexical"] == [a.id]
    assert result.lists["vector"][:2] == [b.id, c.id]
    assert result.lists["topic"] == [b.id, c.id]

    by_id = {candidate.item.id: candidate for candidate in result.candidates}
    assert by_id[b.id].fused_score == pytest.approx(2 / (k + 1))
    assert by_id[c.id].fused_score == pytest.approx(2 / (k + 2))
    assert by_id[a.id].fused_score == pytest.approx(1 / (k + 1) + 1 / (k + 3))
    assert [candidate.item.id for candidate in result.candidates] == [b.id, a.id, c.id]
    assert by_id[a.id].lexical_rank == 1 and by_id[a.id].vector_rank == 3
    assert by_id[a.id].topic_rank is None
    assert by_id[b.id].vector_rank == 1 and by_id[b.id].topic_rank == 1
    assert result.best_vec == pytest.approx(by_id[b.id].vector_score)

    free_text = retrieve(
        db_session, ORG, query_text="ISO 27001", query_vector=unit(1.0), topics=[]
    )
    assert "topic" not in free_text.lists
    assert free_text.candidates[0].topic_rank is None


def test_retrieve_collapses_clusters_keeping_the_highest_fused_member(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session)
    canonical = make_item(
        db_session, doc, question_text="Describe your DSPT status.",
        answer_text="Standards met for 2024-25.", is_canonical=True,
        answer_embedding=unit(0.7, 0.714), question_embedding=None,
    )
    variant = make_item(
        db_session, doc, question_text="Confirm your DSPT status.",
        answer_text="DSPT: standards met, 2024-25, ODS X26.", canonical_id=canonical.id,
        answer_embedding=unit(0.95, 0.312), question_embedding=None,
    )
    other = make_item(
        db_session, doc, answer_text="Cyber Essentials Plus certificate held.",
        answer_embedding=unit(0.5, 0.866), question_embedding=None,
    )
    result = retrieve(
        db_session, ORG, query_text="DSPT status", query_vector=unit(1.0), topics=[]
    )
    ids = [candidate.item.id for candidate in result.candidates]
    assert variant.id in ids and canonical.id not in ids
    assert other.id in ids
    kept = next(candidate for candidate in result.candidates if candidate.item.id == variant.id)
    assert kept.cluster_id == canonical.id


def test_retrieve_respects_top_k_and_best_vec_covers_lexical_only_candidates(
    db_session: Session, fake_embeddings, settings_override
) -> None:
    settings_override(candidates_per_list=1, top_k_synthesis=2)
    doc = make_document(db_session)
    # The vector list holds one item; the lexical hit is outside it yet must still carry a
    # vector score, and best_vec must see it.
    runner_up = make_item(
        db_session, doc, answer_text="closest by vector", answer_embedding=unit(0.6, 0.8),
        question_embedding=None,
    )
    lexical_hit = make_item(
        db_session, doc, question_text="MHRA registration", answer_text="MHRA number 1234.",
        answer_embedding=unit(0.9, 0.436), question_embedding=None,
    )
    make_item(
        db_session, doc, answer_text="noise", answer_embedding=unit(0.1, 0.995),
        question_embedding=None,
    )
    result = retrieve(
        db_session, ORG, query_text="MHRA registration", query_vector=unit(1.0), topics=[]
    )
    assert result.lists["vector"] == [lexical_hit.id]
    assert result.lists["lexical"] == [lexical_hit.id]
    assert len(result.candidates) <= 2
    assert runner_up.id not in [c.item.id for c in result.candidates]

    settings_override(candidates_per_list=1, top_k_synthesis=8)
    # Make the vector list's single slot go to another item so lexical_hit is lexical-only.
    make_item(
        db_session, doc, answer_text="exact vector match", answer_embedding=unit(1.0),
        question_embedding=None,
    )
    result = retrieve(
        db_session, ORG, query_text="MHRA registration", query_vector=unit(1.0), topics=[]
    )
    lexical_only = next(c for c in result.candidates if c.item.id == lexical_hit.id)
    assert lexical_only.vector_rank is None
    assert lexical_only.vector_score == pytest.approx(0.9, abs=1e-3)
    assert result.best_vec == pytest.approx(1.0, abs=1e-6)


def test_retrieve_with_an_empty_library_returns_nothing(
    db_session: Session, fake_embeddings
) -> None:
    result = retrieve(
        db_session, ORG, query_text="anything at all", query_vector=unit(1.0), topics=["x"]
    )
    assert result.candidates == [] and result.best_vec == 0.0


def test_get_query_vector_uses_the_stored_embedding_for_the_question_text(
    db_session: Session, fake_embeddings
) -> None:
    stored = unit(0.6, 0.8)
    question = make_question(db_session, text="Describe your clinical safety case.",
                             embedding=stored)
    assert get_query_vector(db_session, question=question) == pytest.approx(stored)
    assert get_query_vector(
        db_session, question=question, text=" Describe your clinical safety case. "
    ) == pytest.approx(stored)

    rewritten = get_query_vector(db_session, question=question, text="Shorten the safety case.")
    assert rewritten == pytest.approx(embed(["Shorten the safety case."])[0])
    assert question.embedding is not None and list(question.embedding) == pytest.approx(stored)

    unembedded = make_question(db_session, text="Not yet embedded.", embedding=None)
    assert get_query_vector(db_session, question=unembedded) == pytest.approx(
        embed(["Not yet embedded."])[0]
    )
    assert unembedded.embedding is None, "embedding at call time is never stored"

    assert get_query_vector(db_session, text="free text") == pytest.approx(embed(["free text"])[0])
    with pytest.raises(ValueError):
        get_query_vector(db_session)
    with pytest.raises(ValueError):
        get_query_vector(db_session, text="   ")

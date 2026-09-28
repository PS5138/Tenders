"""Coverage triage: the floor path (no LLM call) and the judgement path (one FAST call)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.client import FakeLLMError
from app.retrieve.coverage import (
    FLOOR_GAP_SUMMARY,
    CoverageJudgement,
    build_judgement_input,
    coverage_for_question,
)
from tests.test_retrieve_helpers import make_document, make_item, make_question, unit

QUESTION = "Describe how your clinical safety case complies with DCB0129."


def _library(session: Session, *, near: int = 4) -> list:
    doc = make_document(session)
    items = []
    for index in range(near):
        items.append(
            make_item(
                session,
                doc,
                question_text=f"Clinical safety case {index}",
                answer_text=f"Our DCB0129 clinical safety case variant {index} covers hazards.",
                topics=["clinical_safety"],
                answer_embedding=unit(0.9 - index * 0.05, 0.4),
                question_embedding=None,
            )
        )
    items.append(
        make_item(
            session, doc, answer_text="Social value: local hiring.", topics=["social_value"],
            answer_embedding=unit(0.0, 1.0), question_embedding=None,
        )
    )
    return items


def test_floor_path_labels_new_without_calling_the_model(
    db_session: Session, fake_llm, fake_embeddings
) -> None:
    _library(db_session)
    # A stored embedding far from every item: best_vec is 0.
    question = make_question(
        db_session, text=QUESTION, topics=["clinical_safety"], embedding=unit(0.0, 0.0, 1.0)
    )
    result = coverage_for_question(db_session, question)

    assert result.coverage == "new"
    assert result.detail["label_source"] == "floor"
    assert result.detail["best_vec"] == pytest.approx(0.0, abs=1e-6)
    assert result.detail["coverage_floor"] == get_settings().coverage_floor
    assert result.detail["gap_summary"] == FLOOR_GAP_SUMMARY
    assert result.detail["model"] is None and result.detail["prompt_version"] is None
    assert result.detail["candidates"], "candidates are still recorded for the harness"
    assert fake_llm.calls == []
    json.dumps(result.detail)  # JSON-able for questions.coverage_detail


def test_floor_is_read_from_settings(
    db_session: Session, fake_llm, fake_embeddings, settings_override
) -> None:
    settings_override(coverage_floor=0.999)
    _library(db_session)
    question = make_question(
        db_session, text=QUESTION, topics=["clinical_safety"], embedding=unit(1.0)
    )
    result = coverage_for_question(db_session, question)
    assert result.coverage == "new" and result.detail["label_source"] == "floor"
    assert result.detail["best_vec"] > 0.9
    assert fake_llm.calls == []


def test_judgement_path_uses_one_fast_call_over_the_top_candidates(
    db_session: Session, fake_llm, fake_embeddings
) -> None:
    items = _library(db_session)
    question = make_question(
        db_session, text=QUESTION, topics=["clinical_safety"], embedding=unit(1.0),
        word_limit=500, mandatory=True,
    )
    fake_llm.register(
        "coverage_judgement",
        {
            "coverage": "partial",
            "gap_summary": "  The candidates do not name the hazard log review cadence. ",
            "gaps": ["hazard log review cadence", "  ", "named clinical safety officer"],
        },
    )
    result = coverage_for_question(db_session, question)

    assert result.coverage == "partial"
    assert result.detail["label_source"] == "llm"
    assert result.detail["gap_summary"] == (
        "The candidates do not name the hazard log review cadence."
    )
    assert result.detail["gaps"] == ["hazard log review cadence", "named clinical safety officer"]
    assert result.detail["model"] == get_settings().model_fast
    assert result.detail["prompt_version"] == "coverage_judgement.v1"
    assert result.detail["best_vec"] == pytest.approx(result.retrieval.best_vec)

    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call.name == "coverage_judgement"
    assert call.model == get_settings().model_fast
    assert call.output_model is CoverageJudgement
    assert "TODO" not in call.system and "covered" in call.system and "partial" in call.system

    top = get_settings().coverage_judgement_top
    judged = result.detail["judged_item_ids"]
    assert judged == [c["item_id"] for c in result.detail["candidates"][:top]]
    assert len(judged) == top
    for item_id in judged:
        assert item_id in call.user
    assert str(items[-1].id) not in call.user, "the off-topic, far item is not sent"
    assert QUESTION in call.user and "word limit: 500" in call.user and "mandatory" in call.user


def test_judgement_may_itself_return_new_above_the_floor(
    db_session: Session, fake_llm, fake_embeddings
) -> None:
    _library(db_session)
    question = make_question(
        db_session, text=QUESTION, topics=["clinical_safety"], embedding=unit(1.0)
    )
    fake_llm.register(
        "coverage_judgement",
        CoverageJudgement(coverage="new", gap_summary="Nothing addresses DCB0129.", gaps=["x"]),
    )
    result = coverage_for_question(db_session, question)
    assert result.coverage == "new"
    assert result.detail["label_source"] == "llm"
    assert result.detail["best_vec"] >= get_settings().coverage_floor


def test_judgement_covered_and_an_unscripted_fake_fails_loudly(
    db_session: Session, fake_llm, fake_embeddings
) -> None:
    _library(db_session)
    question = make_question(
        db_session, text=QUESTION, topics=["clinical_safety"], embedding=unit(1.0)
    )
    with pytest.raises(FakeLLMError):
        coverage_for_question(db_session, question)

    fake_llm.register("coverage_judgement", {"coverage": "covered"})
    result = coverage_for_question(db_session, question)
    assert result.coverage == "covered"
    assert result.detail["gap_summary"] == "" and result.detail["gaps"] == []


def test_judgement_input_lists_pairs_and_chunks_differently(
    db_session: Session, fake_embeddings
) -> None:
    doc = make_document(db_session)
    pair = make_item(db_session, doc, question_text="Q?", answer_text="A.", topics=["a"])
    chunk = make_item(db_session, doc, answer_text="Excerpt text.", topics=[])
    question = make_question(db_session, text="Anything?", topics=["a"], embedding=unit(1.0))
    from app.retrieve.fusion import Candidate

    text = build_judgement_input(
        question,
        [Candidate(item=pair, fused_score=0.1, vector_score=0.5),
         Candidate(item=chunk, fused_score=0.05, vector_score=0.4)],
    )
    assert "[C1]" in text and "Past question: Q?" in text and "Past answer: A." in text
    assert "[C2]" in text and "Excerpt: Excerpt text." in text
    assert "CANDIDATES (2)" in text

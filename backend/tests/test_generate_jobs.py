# ruff: noqa: F811  (pytest fixtures are imported and then named as parameters)
"""The draft_all job: eligibility, per-item results and progress, never touching questions
with a human version."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Answer
from app.generate.jobs import draft_all, eligible_questions, run_draft_all
from app.jobs import enqueue, get_handler
from app.review.support import summarise_support
from tests.test_generate_fixtures import (
    build_library,
    build_question,
    build_tender,
    default_synthesis_lines,
    fake_candidate,
    script_entailment,
    script_synthesis,
    stubbed_modules,  # noqa: F401  (fixture)
)


def test_draft_all_is_registered() -> None:
    assert get_handler("draft_all") is draft_all


def _human_version(session: Session, question) -> Answer:  # noqa: ANN001
    answer = Answer(
        org_id=question.org_id,
        question_id=question.id,
        version=1,
        author_type="user",
        author_name="Jane",
        text="Written by hand.",
        word_count=3,
        segments=[],
        support_summary=summarise_support([]),
        is_current=True,
    )
    session.add(answer)
    session.flush()
    return answer


def test_draft_all_drafts_eligible_questions_and_records_results(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    tender = build_tender(db_session)
    covered = build_question(
        db_session, tender=tender, text="Covered one.", number="1", order_index=1
    )
    partial = build_question(
        db_session,
        tender=tender,
        text="Partial one.",
        coverage="partial",
        number="2",
        order_index=2,
    )
    new = build_question(
        db_session, tender=tender, text="New one.", coverage="new", number="3", order_index=3
    )
    pricing = build_question(
        db_session, tender=tender, text="Price.", response_type="pricing", number="4", order_index=4
    )
    drafted_already = build_question(
        db_session, tender=tender, text="Drafted.", status="ai_draft", number="5", order_index=5
    )
    with_human = build_question(
        db_session, tender=tender, text="Human one.", number="6", order_index=6
    )
    _human_version(db_session, with_human)
    other_tender_question = build_question(db_session, text="Elsewhere.", number="7")

    assert [q.id for q in eligible_questions(db_session, tender.id, include_new=False)] == [
        covered.id,
        partial.id,
        with_human.id,
    ]
    assert [q.id for q in eligible_questions(db_session, tender.id, include_new=True)] == [
        covered.id,
        partial.id,
        new.id,
        with_human.id,
    ]

    stubbed_modules.candidates = [fake_candidate(library.item)]
    script_synthesis(stubbed_modules.fake_llm, default_synthesis_lines(library))
    script_entailment(stubbed_modules.fake_llm)
    job = enqueue(
        db_session,
        "draft_all",
        {"tender_id": str(tender.id), "include_new": False, "actor": "test user"},
    )

    run_draft_all(db_session, job, concurrency=1)

    assert job.total == 3 and job.done == 3
    outcomes = {result["item_id"]: result for result in job.results}
    assert outcomes[str(covered.id)]["outcome"] == "drafted"
    assert outcomes[str(partial.id)]["outcome"] == "drafted"
    assert outcomes[str(with_human.id)] == {
        "item_id": str(with_human.id),
        "outcome": "skipped",
        "detail": "has a human version",
    }
    for question in (covered, partial):
        db_session.refresh(question)
        assert question.status == "ai_draft"
        answer = db_session.get(Answer, outcomes[str(question.id)]["detail"])
        assert answer is not None and answer.is_current and answer.author_type == "ai"
    for question in (new, pricing, drafted_already, other_tender_question):
        db_session.refresh(question)
        assert (
            db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all() == []
        )
    db_session.refresh(with_human)
    assert with_human.status == "not_started"
    human_versions = db_session.scalars(
        select(Answer).where(Answer.question_id == with_human.id)
    ).all()
    assert len(human_versions) == 1 and human_versions[0].author_type == "user"
    # Events from draft-all name the person who started it.
    from app.db.models import Event

    created = db_session.scalars(
        select(Event).where(Event.entity_id == covered.id, Event.event_type == "answer_created")
    ).all()
    assert [event.actor for event in created] == ["test user"]


def test_draft_all_records_per_item_failures_without_raising(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    tender = build_tender(db_session)
    first = build_question(db_session, tender=tender, text="First.", number="1", order_index=1)
    second = build_question(db_session, tender=tender, text="Second.", number="2", order_index=2)
    stubbed_modules.candidates = [fake_candidate(library.item)]
    lines = default_synthesis_lines(library)
    calls = {"n": 0}

    def flaky(**kwargs):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call fails")
        return "".join(
            __import__("json").dumps(line) + "\n" for line in lines if isinstance(line, dict)
        )

    stubbed_modules.fake_llm.register("synthesis", flaky)
    script_entailment(stubbed_modules.fake_llm)
    job = enqueue(
        db_session,
        "draft_all",
        {"tender_id": str(tender.id), "include_new": True, "actor": "test user"},
    )
    run_draft_all(db_session, job, concurrency=1)

    assert job.total == 2 and job.done == 2
    assert job.results[0]["item_id"] == str(first.id)
    assert job.results[0]["outcome"] == "failed"
    assert "first call fails" in job.results[0]["detail"]
    assert job.results[1] == {
        "item_id": str(second.id),
        "outcome": "drafted",
        "detail": job.results[1]["detail"],
    }
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.status == "not_started" and second.status == "ai_draft"


def test_draft_all_with_nothing_eligible(db_session: Session, stubbed_modules) -> None:  # noqa: ANN001
    tender = build_tender(db_session)
    job = enqueue(db_session, "draft_all", {"tender_id": str(tender.id), "actor": "test user"})
    run_draft_all(db_session, job)
    assert job.total == 0 and job.done == 0 and job.results == []

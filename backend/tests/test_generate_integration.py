"""The draft pipeline against the real retrieval (owner C) and review (owner F) modules, with
only the LLM and embeddings faked. Skipped when either module has not landed."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Answer, Event
from app.generate import pipeline
from app.generate.errors import Conflict409
from tests.test_generate_fixtures import (
    EXPECTED_TEXTS,
    QUESTION_TEXT,
    build_library,
    build_question,
    default_synthesis_lines,
    script_entailment,
    script_synthesis,
)

pytestmark = pytest.mark.integration

try:  # the modules this test depends on
    import app.retrieve.facts  # noqa: F401
    import app.retrieve.query  # noqa: F401
    import app.retrieve.search  # noqa: F401
    from app.review.support import recompute_support  # noqa: F401
    from app.review.transitions import allowed_transitions
    from app.review.versions import set_current_ai_version  # noqa: F401
except ImportError as exc:  # pragma: no cover - depends on the other owners' progress
    pytest.skip(f"retrieval or review module not available: {exc}", allow_module_level=True)


def test_card_draft_end_to_end_with_real_retrieval_and_review(
    db_session: Session,
    fake_llm,
    fake_embeddings,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    question = build_question(db_session, text=QUESTION_TEXT)
    db_session.flush()
    script_synthesis(fake_llm, default_synthesis_lines(library))
    script_entailment(fake_llm, "supported")
    events: list[dict] = []

    answer = pipeline.draft_question(db_session, question, actor="test user", emit=events.append)
    db_session.flush()

    types = [event["type"] for event in events]
    assert types[0] == "verbatim", "the identical past question triggers the verbatim offer"
    assert types.count("segment") == len(EXPECTED_TEXTS) == types.count("support")
    assert answer.verbatim_offer_item_id == library.item.id
    assert answer.is_current and answer.version == 1
    assert [s["text"] for s in answer.segments] == EXPECTED_TEXTS
    assert answer.support_summary["substantive"] == 5
    assert answer.fact_checklist and answer.fact_checklist[0]["fact_id"] == str(library.fact.id)

    db_session.refresh(question)
    assert question.status == "ai_draft", "moved through the transition function"
    assert question.needs_review is False
    kinds = [
        event.event_type
        for event in db_session.scalars(
            select(Event).where(Event.entity_id == question.id).order_by(Event.created_at, Event.id)
        )
    ]
    assert kinds.count("answer_created") == 1
    assert kinds.count("status_changed") == 1
    transitions = {t["to"]: t for t in allowed_transitions(db_session, question)}
    assert transitions["writer_edited"]["allowed"] is True
    assert transitions["ai_draft"]["allowed"] is False

    # A second draft displaces nothing at ai_draft; at approved it needs the flag.
    question.status = "approved"  # fixture short-cut; production writes go through transition()
    db_session.flush()
    with pytest.raises(Conflict409) as excinfo:
        pipeline.draft_question(db_session, question, actor="test user")
    assert excinfo.value.code == "displacement"
    assert excinfo.value.current_answer["id"] == str(answer.id)
    second = pipeline.draft_question(db_session, question, actor="test user", confirm_displace=True)
    assert second.version == 2 and second.is_current
    db_session.refresh(answer)
    assert answer.is_current is False
    assert (
        len(db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all()) == 2
    )

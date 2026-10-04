"""The questions, answers and threads endpoints through the ASGI app."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ACTOR_MAX_LENGTH
from app.api.schemas import AnswerRecord, QuestionDetail, ThreadRecord
from app.config import FIXTURE_DOCUMENT_ID
from app.db.models import Message, Thread
from app.review.transitions import transition
from app.review.versions import set_current_ai_version
from tests.test_review_helpers import (
    ACTOR,
    doc_source,
    fixture_sections,
    make_answer,
    make_question,
    make_tender,
    seg,
    supported_answer,
)


def _mixed(db_session: Session):  # noqa: ANN202
    section = fixture_sections(db_session)[0]
    question = make_question(db_session)
    answer = make_answer(
        db_session,
        question,
        [
            seg(0, "Supported sentence.", "supported", [doc_source(section)]),
            seg(1, "Weak sentence.", "weak", [doc_source(section)]),
            seg(2, "Connective.", "connective"),
        ],
        gaps=["A named deputy."],
    )
    set_current_ai_version(db_session, question, answer, ACTOR)
    db_session.flush()
    return question, answer


async def test_get_question_detail(app_client: httpx.AsyncClient, db_session: Session) -> None:
    question, answer = _mixed(db_session)
    response = await app_client.get(f"/questions/{question.id}")
    assert response.status_code == 200, response.text
    body = QuestionDetail.model_validate(response.json())
    assert body.id == question.id
    assert body.thread_id is not None
    assert body.status == "ai_draft"
    assert body.draft_in_progress is False
    assert body.current_answer is not None and body.current_answer.id == answer.id
    assert [s.support_status for s in body.current_answer.segments] == [
        "supported", "weak", "connective",
    ]
    assert body.current_answer.gaps == ["A named deputy."]
    transitions = {t.to: t for t in body.allowed_transitions}
    assert transitions["ai_draft"].blockers[0].kind == "system_only"
    assert transitions["writer_edited"].allowed is True
    assert [b.model_dump(exclude_none=True) for b in transitions["sme_verified"].blockers] == [
        {"kind": "segment", "index": 1}
    ]
    assert [b.model_dump(exclude_none=True) for b in transitions["approved"].blockers] == [
        {"kind": "segment", "index": 1},
        {"kind": "gap", "gap": "A named deputy."},
    ]
    missing = await app_client.get(f"/questions/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_patch_question_refuses_an_over_long_assignee(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    question, _ = _mixed(db_session)
    too_long = await app_client.patch(
        f"/questions/{question.id}", json={"assignee": "a" * (ACTOR_MAX_LENGTH + 1)}
    )
    assert too_long.status_code == 422, too_long.text
    db_session.refresh(question)
    assert question.assignee is None
    at_limit = await app_client.patch(
        f"/questions/{question.id}", json={"assignee": "b" * ACTOR_MAX_LENGTH}
    )
    assert at_limit.status_code == 200, at_limit.text
    assert len(at_limit.json()["assignee"]) == ACTOR_MAX_LENGTH


async def test_patch_question(app_client: httpx.AsyncClient, db_session: Session) -> None:
    question, _ = _mixed(db_session)
    blocked = await app_client.patch(
        f"/questions/{question.id}", json={"status": "sme_verified", "assignee": "Jane"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["blockers"] == [{"kind": "segment", "index": 1}]
    # The other fields are never gated: they are applied with the refusal, and the 409 carries
    # the row as it now stands. The status itself is unchanged.
    assert blocked.json()["question"]["assignee"] == "Jane"
    assert blocked.json()["question"]["status"] == "ai_draft"
    db_session.refresh(question)
    assert question.assignee == "Jane" and question.status == "ai_draft"
    blocked_events = (await app_client.get(f"/questions/{question.id}/events")).json()
    assert [event["event_type"] for event in blocked_events][0] == "assigned"

    ok = await app_client.patch(
        f"/questions/{question.id}",
        json={
            "status": "writer_edited",
            "assignee": "Jane Doe",
            "compliance_class": "B",
            "compliant_by": "2026-12-01",
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "writer_edited"
    assert body["assignee"] == "Jane Doe"
    assert body["compliance_class"] == "B" and body["compliant_by"] == "2026-12-01"

    events = (await app_client.get(f"/questions/{question.id}/events")).json()
    kinds = [event["event_type"] for event in events]
    assert kinds[:3] == ["status_changed", "compliance_class_set", "assigned"], "newest first"
    assert all(event["actor"] == ACTOR for event in events[:3])

    system_only = await app_client.patch(f"/questions/{question.id}", json={"status": "ai_draft"})
    assert system_only.status_code == 409
    assert system_only.json()["blockers"] == [{"kind": "system_only"}]

    # Clearing the compliance class is never gated.
    cleared = await app_client.patch(
        f"/questions/{question.id}", json={"compliance_class": None, "compliant_by": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["compliance_class"] is None


async def test_evidence_add_and_remove(app_client: httpx.AsyncClient, db_session: Session) -> None:
    question = make_question(db_session)
    db_session.flush()
    added = await app_client.post(
        f"/questions/{question.id}/evidence",
        json={"document_id": str(FIXTURE_DOCUMENT_ID), "note": "Safety case report."},
    )
    assert added.status_code == 201, added.text
    evidence = added.json()["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["document_id"] == str(FIXTURE_DOCUMENT_ID)
    assert evidence[0]["filename"] and evidence[0]["note"] == "Safety case report."
    events = (await app_client.get(f"/questions/{question.id}/events")).json()
    assert events[0]["event_type"] == "evidence_added"

    unknown = await app_client.post(
        f"/questions/{question.id}/evidence", json={"document_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 404

    removed = await app_client.delete(
        f"/questions/{question.id}/evidence/{FIXTURE_DOCUMENT_ID}"
    )
    assert removed.status_code == 204
    detail = (await app_client.get(f"/questions/{question.id}")).json()
    assert detail["evidence"] == []
    again = await app_client.delete(f"/questions/{question.id}/evidence/{FIXTURE_DOCUMENT_ID}")
    assert again.status_code == 404


async def test_answers_list_and_human_edit(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    question, answer = _mixed(db_session)
    listed = await app_client.get(f"/questions/{question.id}/answers")
    assert listed.status_code == 200
    assert [a["version"] for a in listed.json()] == [1]
    AnswerRecord.model_validate(listed.json()[0])

    stale = await app_client.post(
        f"/questions/{question.id}/answers",
        json={"text": "Whatever.", "base_version_id": str(uuid.uuid4())},
    )
    assert stale.status_code == 409
    assert stale.json()["current_answer"]["id"] == str(answer.id)
    assert len((await app_client.get(f"/questions/{question.id}/answers")).json()) == 1

    saved = await app_client.post(
        f"/questions/{question.id}/answers",
        json={"text": answer.text + " A new sentence.", "base_version_id": str(answer.id)},
    )
    assert saved.status_code == 201, saved.text
    body = saved.json()
    new_answer = AnswerRecord.model_validate(body["answer"])
    assert new_answer.version == 2 and new_answer.is_current
    assert new_answer.author_type == "user" and new_answer.author_name == ACTOR
    assert [s.support_status for s in new_answer.segments] == [
        "supported", "weak", "connective", "human_authored",
    ]
    assert body["question"]["status"] == "writer_edited"
    assert body["question"]["current_answer"]["id"] == str(new_answer.id)
    versions = (await app_client.get(f"/questions/{question.id}/answers")).json()
    assert [(a["version"], a["is_current"]) for a in versions] == [(1, False), (2, True)]


async def test_human_edit_with_no_sentences_is_a_422(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    """Whitespace passes the body's ``min_length`` but the authoritative splitter finds no
    sentence in it; the API answers 422 with the review layer's message and writes nothing."""
    question, answer = _mixed(db_session)
    for text in (" ", "   \n\t \n"):
        response = await app_client.post(
            f"/questions/{question.id}/answers",
            json={"text": text, "base_version_id": str(answer.id)},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "The answer text contains no sentences."
    versions = (await app_client.get(f"/questions/{question.id}/answers")).json()
    assert [(a["version"], a["is_current"]) for a in versions] == [(1, True)]
    detail = (await app_client.get(f"/questions/{question.id}")).json()
    assert detail["status"] == "ai_draft"
    assert detail["current_answer"]["id"] == str(answer.id)


async def test_attest_and_dispute_endpoints(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    question, answer = _mixed(db_session)
    refused = await app_client.post(f"/answers/{answer.id}/segments/0/attest", json={})
    assert refused.status_code == 409

    attested = await app_client.post(
        f"/answers/{answer.id}/segments/1/attest", json={"note": "Checked the register."}
    )
    assert attested.status_code == 200, attested.text
    body = attested.json()
    segment = body["answer"]["segments"][1]
    assert segment["support_status"] == "supported"
    assert segment["sources"][-1]["source_type"] == "human_attestation"
    assert segment["sources"][-1]["attested_by"] == ACTOR
    assert body["question"]["id"] == str(question.id)
    assert body["question"]["current_answer"]["support_summary"]["score"] == 1.0

    missing_note = await app_client.post(f"/answers/{answer.id}/segments/0/dispute", json={})
    assert missing_note.status_code == 422
    blank_note = await app_client.post(
        f"/answers/{answer.id}/segments/0/dispute", json={"note": "   "}
    )
    assert blank_note.status_code == 422, "a note of only spaces is refused, not a 500"
    disputed = await app_client.post(
        f"/answers/{answer.id}/segments/0/dispute", json={"note": "Out of date."}
    )
    assert disputed.status_code == 200, disputed.text
    body = disputed.json()
    assert body["answer"]["segments"][0]["support_status"] == "unsupported"
    assert body["answer"]["segments"][0]["dispute"]["note"] == "Out of date."
    assert body["question"]["needs_review"] is True

    not_supported = await app_client.post(
        f"/answers/{answer.id}/segments/0/dispute", json={"note": "Again."}
    )
    assert not_supported.status_code == 409
    out_of_range = await app_client.post(f"/answers/{answer.id}/segments/9/attest", json={})
    assert out_of_range.status_code == 404
    unknown = await app_client.post(f"/answers/{uuid.uuid4()}/segments/0/attest", json={})
    assert unknown.status_code == 404


async def test_acknowledge_gap_endpoint(app_client: httpx.AsyncClient, db_session: Session) -> None:
    question, _ = _mixed(db_session)
    wrong = await app_client.post(
        f"/questions/{question.id}/gaps/acknowledge", json={"gap": "Not a gap", "note": "x"}
    )
    assert wrong.status_code == 422
    ok = await app_client.post(
        f"/questions/{question.id}/gaps/acknowledge",
        json={"gap": "a named deputy.", "note": "Will name one before submission."},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["gap_acknowledgements"][0]["acknowledged_by"] == ACTOR
    approved = next(t for t in body["allowed_transitions"] if t["to"] == "approved")
    assert {b["kind"] for b in approved["blockers"]} == {"segment"}, "the gap no longer blocks"


async def test_comments(app_client: httpx.AsyncClient, db_session: Session) -> None:
    question = make_question(db_session)
    db_session.flush()
    blank = await app_client.post(f"/questions/{question.id}/comments", json={"text": " \n "})
    assert blank.status_code == 422
    created = await app_client.post(
        f"/questions/{question.id}/comments", json={"text": "  Check the DSPT year. "}
    )
    assert created.status_code == 201, created.text
    assert created.json()["author"] == ACTOR and created.json()["text"] == "Check the DSPT year."
    detail = (await app_client.get(f"/questions/{question.id}")).json()
    assert [c["text"] for c in detail["comments"]] == ["Check the DSPT year."]
    events = (await app_client.get(f"/questions/{question.id}/events")).json()
    assert events[0]["event_type"] == "comment_added"


async def test_threads_and_save_as_answer(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    tender = make_tender(db_session)
    db_session.flush()
    created = await app_client.post("/threads", json={"tender_id": str(tender.id)})
    assert created.status_code == 201, created.text
    thread = ThreadRecord.model_validate(created.json())
    assert thread.question_id is None and thread.tender_id == tender.id
    assert thread.reply_in_progress is False and thread.messages == []
    unknown = await app_client.post("/threads", json={"tender_id": str(uuid.uuid4())})
    assert unknown.status_code == 404

    question = make_question(db_session, tender)
    set_current_ai_version(db_session, question, supported_answer(db_session, question), ACTOR)
    transition(db_session, question, "writer_edited", ACTOR)
    question_thread = db_session.scalars(
        select(Thread).where(Thread.question_id == question.id)
    ).one()
    section = fixture_sections(db_session)[0]
    message = Message(
        org_id=question.org_id,
        thread_id=question_thread.id,
        role="assistant",
        content="Shorter answer.",
        segments=[seg(0, "Shorter answer.", "supported", [doc_source(section)])],
        gaps=[],
        fact_checklist=[],
        model="claude-opus-5",
        prompt_version="synthesis.v2",
    )
    db_session.add(message)
    db_session.flush()

    fetched = await app_client.get(f"/threads/{question_thread.id}")
    assert fetched.status_code == 200, fetched.text
    body = ThreadRecord.model_validate(fetched.json())
    assert body.question_id == question.id
    assert [m.role for m in body.messages] == ["assistant"]
    assert body.messages[0].segments is not None and body.messages[0].answer_id is None

    refused = await app_client.post(f"/messages/{message.id}/save-as-answer", json={})
    assert refused.status_code == 409
    assert refused.json()["current_answer"]["version"] == 1
    # One code for the plan's one displacement rule, shared with card draft and accept-verbatim.
    assert refused.json()["code"] == "displacement"
    assert refused.json()["status"] == "writer_edited"
    saved = await app_client.post(
        f"/messages/{message.id}/save-as-answer", json={"confirm_displace": True}
    )
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["answer"]["version"] == 2 and body["answer"]["author_type"] == "ai"
    assert body["answer"]["text"] == "Shorter answer."
    assert body["question"]["status"] == "ai_draft"
    refetched = (await app_client.get(f"/threads/{question_thread.id}")).json()
    assert refetched["messages"][0]["answer_id"] == body["answer"]["id"]


async def test_patch_echoing_a_system_only_status_is_a_no_op(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    """A request for the current status is a no-op that writes no event (plan: transition
    matrix), even when that status is system-only, so a full-row PATCH repeating ``ai_draft``
    or ``not_started`` still applies its other fields instead of returning 409."""
    question, _ = _mixed(db_session)
    assert question.status == "ai_draft"
    before = (await app_client.get(f"/questions/{question.id}/events")).json()
    status_changes_before = sum(e["event_type"] == "status_changed" for e in before)

    echoed = await app_client.patch(
        f"/questions/{question.id}", json={"status": "ai_draft", "assignee": "Jane"}
    )
    assert echoed.status_code == 200, echoed.text
    detail = echoed.json()
    assert detail["status"] == "ai_draft" and detail["assignee"] == "Jane"
    events = (await app_client.get(f"/questions/{question.id}/events")).json()
    assert sum(e["event_type"] == "assigned" for e in events) == 1
    assert sum(e["event_type"] == "status_changed" for e in events) == status_changes_before
    # The matrix still reports the target as system-only for a person.
    rows = {row["to"]: row for row in detail["allowed_transitions"]}
    assert rows["ai_draft"]["allowed"] is False
    assert rows["ai_draft"]["blockers"][0]["kind"] == "system_only"

    fresh = make_question(db_session)
    echoed = await app_client.patch(
        f"/questions/{fresh.id}", json={"status": "not_started", "assignee": "Jane"}
    )
    assert echoed.status_code == 200, echoed.text
    assert echoed.json()["status"] == "not_started" and echoed.json()["assignee"] == "Jane"
    events = (await app_client.get(f"/questions/{fresh.id}/events")).json()
    assert [e["event_type"] for e in events] == ["assigned"]
    # A different system-only target is still refused; the status stays put while the
    # ungated assignee is applied.
    refused = await app_client.patch(
        f"/questions/{fresh.id}", json={"status": "ai_draft", "assignee": "Someone Else"}
    )
    assert refused.status_code == 409
    assert refused.json()["blockers"] == [{"kind": "system_only"}]
    db_session.refresh(fresh)
    assert fresh.status == "not_started" and fresh.assignee == "Someone Else"


async def test_save_as_answer_refuses_a_pricing_question(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    """Pricing questions receive no AI draft (plan: Review pipeline): save-as-answer is 409
    and nothing is written, like card draft, accept-verbatim and draft-all."""
    question = make_question(db_session, response_type="pricing")
    thread = db_session.scalars(select(Thread).where(Thread.question_id == question.id)).one()
    section = fixture_sections(db_session)[0]
    message = Message(
        org_id=question.org_id,
        thread_id=thread.id,
        role="assistant",
        content="Our price is competitive.",
        segments=[seg(0, "Our price is competitive.", "supported", [doc_source(section)])],
        gaps=[],
        fact_checklist=[],
        model="claude-opus-5",
        prompt_version="synthesis.v2",
    )
    db_session.add(message)
    db_session.flush()

    refused = await app_client.post(
        f"/messages/{message.id}/save-as-answer", json={"confirm_displace": True}
    )
    assert refused.status_code == 409, refused.text
    assert "Pricing" in refused.json()["detail"]
    assert (await app_client.get(f"/questions/{question.id}/answers")).json() == []
    detail = (await app_client.get(f"/questions/{question.id}")).json()
    assert detail["status"] == "not_started" and detail["current_answer"] is None
    refetched = (await app_client.get(f"/threads/{thread.id}")).json()
    assert refetched["messages"][0]["answer_id"] is None

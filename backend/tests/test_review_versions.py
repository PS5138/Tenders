"""Displacement rule and the human edit re-alignment (plan: Editing behaviour)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.fixture_data import build_fixture
from app.db.models import Answer
from app.review import versions
from app.review.transitions import current_answer, transition
from app.review.versions import (
    DisplacementRequired,
    EmptyEdit,
    StaleBaseVersion,
    save_human_edit,
    set_current_ai_version,
)
from tests.test_review_helpers import (
    ACTOR,
    events_for,
    make_answer,
    make_question,
    supported_answer,
)


def _plan_fixture_answer(db_session: Session):  # noqa: ANN202
    """The plan's fixture answer persisted as the current version of a fresh question: one
    segment in every support status, an attestation, a dispute, gaps and a fact checklist."""
    bundle = build_fixture(db_session)
    question = make_question(db_session)
    answer = make_answer(
        db_session,
        question,
        [dict(s) for s in bundle.answer["segments"]],
        gaps=list(bundle.answer["gaps"]),
        fact_checklist=[dict(e) for e in bundle.answer["fact_checklist"]],
    )
    set_current_ai_version(db_session, question, answer, ACTOR)
    return question, answer


# --- Displacement -------------------------------------------------------------------------------


def test_ai_version_replaces_ai_draft_without_confirmation(db_session: Session) -> None:
    question = make_question(db_session)
    first = supported_answer(db_session, question)
    set_current_ai_version(db_session, question, first, ACTOR)
    second = make_answer(db_session, question, list(first.segments), current=False)
    set_current_ai_version(db_session, question, second, ACTOR)
    db_session.refresh(first)
    assert second.is_current is True and first.is_current is False
    assert second.version == 2
    assert question.status == "ai_draft"


def test_displacement_requires_confirmation_past_ai_draft(db_session: Session) -> None:
    question = make_question(db_session)
    first = supported_answer(db_session, question)
    set_current_ai_version(db_session, question, first, ACTOR)
    transition(db_session, question, "sme_verified", ACTOR)

    second = Answer(
        org_id=question.org_id,
        question_id=question.id,
        author_type="ai",
        text=first.text,
        word_count=first.word_count,
        segments=list(first.segments),
    )
    with pytest.raises(DisplacementRequired) as excinfo:
        set_current_ai_version(db_session, question, second, ACTOR, confirm_displace=False)
    assert excinfo.value.current_answer is not None
    assert excinfo.value.current_answer.id == first.id
    assert question.status == "sme_verified"
    assert current_answer(db_session, question).id == first.id

    set_current_ai_version(db_session, question, second, ACTOR, confirm_displace=True)
    db_session.refresh(first)
    assert second.is_current is True and second.version == 2
    assert first.is_current is False, "the displaced version stays in the history"
    assert question.status == "ai_draft"
    history = db_session.scalars(
        select(Answer).where(Answer.question_id == question.id).order_by(Answer.version)
    ).all()
    assert [a.version for a in history] == [1, 2]


# --- Human edit ---------------------------------------------------------------------------------


def test_unchanged_text_keeps_every_segment_and_moves_to_writer_edited(
    db_session: Session,
) -> None:
    question, base = _plan_fixture_answer(db_session)
    transition(db_session, question, "writer_edited", ACTOR)
    # Attest the human-authored one so the base can be approved; then edit from approved.
    from app.review.actions import attest

    for segment in base.segments:
        if segment["support_status"] in {"weak", "unsupported", "human_authored"}:
            attest(db_session, base, segment["index"], ACTOR, None)
    question.gap_acknowledgements = [
        {"gap": gap, "acknowledged_by": ACTOR, "note": None, "at": "x"} for gap in base.gaps
    ]
    question.needs_review = False
    transition(db_session, question, "approved", ACTOR)
    statuses_before = [s["support_status"] for s in base.segments]

    edited = save_human_edit(db_session, question, base.text, base.id, ACTOR)

    assert [s["support_status"] for s in edited.segments] == statuses_before
    assert [s["text"] for s in edited.segments] == [s["text"] for s in base.segments]
    assert [s["sources"] for s in edited.segments] == [s["sources"] for s in base.segments]
    assert [s["index"] for s in edited.segments] == list(range(len(base.segments)))
    assert edited.is_current is True and edited.version == base.version + 1
    assert edited.author_type == "user" and edited.author_name == ACTOR
    assert edited.model is None and edited.prompt_version is None
    assert edited.gaps == base.gaps and edited.fact_checklist == base.fact_checklist
    assert edited.text == base.text
    assert question.status == "writer_edited", "down from approved, whatever it was before"
    db_session.refresh(base)
    assert base.is_current is False
    changes = events_for(db_session, question.id, "status_changed")
    assert (changes[-1].payload["from"], changes[-1].payload["to"]) == ("approved", "writer_edited")


def test_edits_realign_per_the_plan(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions, "_load_verifier", lambda: None)  # no entailment available
    question, base = _plan_fixture_answer(db_session)
    segments = base.segments
    disputed = next(s for s in segments if s["dispute"] is not None)
    attested = next(
        s for s in segments if any(x["source_type"] == "human_attestation" for x in s["sources"])
    )
    supported = segments[0]
    rewritten = segments[3]

    text = base.text
    # A changed sentence: one word swapped keeps the pair above realign_min.
    text = text.replace(supported["text"], supported["text"].replace("maintains", "keeps"))
    # A rewritten sentence: nothing left to pair with.
    text = text.replace(rewritten["text"], "Our hazard process is entirely different now.")
    # Changing the attested sentence drops the attestation; changing the disputed one drops
    # the dispute.
    text = text.replace(attested["text"], attested["text"].replace("annually", "every year"))
    text = text.replace(disputed["text"], disputed["text"].replace("2024-25", "2025-26"))
    # A brand-new sentence at the end.
    text = text + " We also run a quarterly clinical safety forum."

    edited = save_human_edit(db_session, question, text, base.id, ACTOR)
    by_text = {s["text"]: s for s in edited.segments}

    changed = by_text[supported["text"].replace("maintains", "keeps")]
    assert changed["support_status"] == "weak"
    assert changed["sources"] == supported["sources"], "document sources kept for re-verification"

    new = by_text["Our hazard process is entirely different now."]
    assert new["support_status"] == "human_authored" and new["sources"] == []
    assert new["kind"] == "substantive"
    assert rewritten["text"] not in by_text, "the unpaired old sentence is dropped"

    attested_now = by_text[attested["text"].replace("annually", "every year")]
    assert not any(x["source_type"] == "human_attestation" for x in attested_now["sources"])
    assert attested_now["support_status"] == "human_authored"

    disputed_now = by_text[disputed["text"].replace("2024-25", "2025-26")]
    assert disputed_now["dispute"] is None
    assert disputed_now["support_status"] == "weak"
    assert disputed_now["sources"] == disputed["sources"]

    appended = by_text["We also run a quarterly clinical safety forum."]
    assert appended["support_status"] == "human_authored"
    assert appended["paragraph"] == edited.segments[-2]["paragraph"]

    # Untouched sentences keep their segments verbatim.
    for original in segments:
        if original["text"] in by_text and original["text"] not in {
            supported["text"], rewritten["text"], attested["text"], disputed["text"],
        }:
            assert by_text[original["text"]]["support_status"] == original["support_status"]
    assert [s["index"] for s in edited.segments] == list(range(len(edited.segments)))
    assert question.status == "writer_edited"
    assert edited.gaps == base.gaps and edited.fact_checklist == base.fact_checklist


def test_reverification_restores_supported_where_entailment_passes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    question, base = _plan_fixture_answer(db_session)
    seen: list[list[dict]] = []

    def fake_verify(session, segments):  # noqa: ANN001, ANN202
        seen.append(segments)
        out = []
        for segment in segments:
            copy = dict(segment)
            copy["support_status"] = "supported"
            copy["sources"] = [
                {**src, "quote": "verified quote"} for src in segment["sources"]
            ]
            out.append(copy)
        return out

    monkeypatch.setattr(versions, "_load_verifier", lambda: fake_verify)
    supported = base.segments[0]
    text = base.text.replace(supported["text"], supported["text"].replace("maintains", "keeps"))
    edited = save_human_edit(db_session, question, text, base.id, ACTOR)

    assert len(seen) == 1 and [s["text"] for s in seen[0]] == [
        supported["text"].replace("maintains", "keeps")
    ], "only the weak-by-edit band is verified, in one call"
    restored = edited.segments[0]
    assert restored["support_status"] == "supported"
    assert restored["sources"][0]["quote"] == "verified quote"


def test_no_entailment_call_when_nothing_became_weak(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    question, base = _plan_fixture_answer(db_session)
    calls: list[int] = []
    monkeypatch.setattr(
        versions, "_load_verifier", lambda: (lambda s, segs: calls.append(1) or segs)
    )
    save_human_edit(db_session, question, base.text + " Entirely new sentence.", base.id, ACTOR)
    assert calls == []


def test_stale_base_version_writes_nothing(db_session: Session) -> None:
    question, base = _plan_fixture_answer(db_session)
    with pytest.raises(StaleBaseVersion) as excinfo:
        save_human_edit(db_session, question, "New text.", uuid.uuid4(), ACTOR)
    assert excinfo.value.current_answer.id == base.id
    with pytest.raises(StaleBaseVersion):
        save_human_edit(db_session, question, "New text.", None, ACTOR)
    assert current_answer(db_session, question).id == base.id
    assert question.status == "ai_draft"


def test_first_human_version_on_a_question_without_answers(db_session: Session) -> None:
    question = make_question(db_session, response_type="pricing")
    answer = save_human_edit(
        db_session, question, "Our price is set out in the schedule.\n\nVAT is excluded.",
        None, ACTOR,
    )
    assert answer.version == 1 and answer.is_current
    assert [s["support_status"] for s in answer.segments] == ["human_authored", "human_authored"]
    assert [s["paragraph"] for s in answer.segments] == [0, 1]
    assert answer.text == "Our price is set out in the schedule.\n\nVAT is excluded."
    assert question.status == "writer_edited"
    assert question.needs_review is False


@pytest.mark.integration
def test_reverification_through_owner_e_verify_segments(db_session: Session, fake_llm) -> None:  # noqa: ANN001
    """End to end through app.generate.verification with the fake LLM: a lightly edited
    sentence whose quote is found in the cited item's slice regains ``supported`` with its
    locator offsets filled; a sentence the entailment call does not pass stays ``weak``."""
    pytest.importorskip("app.generate.verification")
    from app.db.models import KnowledgeItem
    from tests.test_review_helpers import doc_source, fixture_sections

    section = fixture_sections(db_session)[0]
    item = KnowledgeItem(
        org_id=section.org_id,
        document_id=section.document_id,
        section_id=section.id,
        answer_start=0,
        answer_end=len(section.text),
        item_type="chunk",
        answer_text=section.text,
        text_verified=True,
        is_canonical=True,
    )
    db_session.add(item)
    db_session.flush()
    quote = section.text.split("\n")[1][:60].strip() or section.text[:60]
    question = make_question(db_session)
    base = make_answer(
        db_session,
        question,
        [
            {
                "index": 0, "paragraph": 0, "kind": "substantive", "dispute": None,
                "text": "The organisation maintains a clinical safety case.",
                "support_status": "supported",
                "sources": [doc_source(section, quote, source_id=item.id)],
            },
            {
                "index": 1, "paragraph": 0, "kind": "substantive", "dispute": None,
                "text": "Hazards are reviewed every month.",
                "support_status": "supported",
                "sources": [doc_source(section, quote, source_id=item.id)],
            },
        ],
    )
    set_current_ai_version(db_session, question, base, ACTOR)
    # Only the first edited sentence passes entailment; the second gets no verdict (weak).
    fake_llm.register("entailment", {"verdicts": [{"id": 0, "verdict": "supported"}]})

    text = base.text.replace("maintains", "keeps").replace("every month", "each month")
    edited = save_human_edit(db_session, question, text, base.id, ACTOR)

    assert [s["support_status"] for s in edited.segments] == ["supported", "weak"]
    restored = edited.segments[0]["sources"][0]
    assert restored["locator"]["start"] is not None
    assert section.text[restored["locator"]["start"] : restored["locator"]["end"]] == (
        restored["quote"]
    )
    assert len([c for c in fake_llm.calls if c.name == "entailment"]) == 1, "one batched call"
    assert question.status == "writer_edited"


def test_whitespace_only_edit_is_refused_and_writes_nothing(db_session: Session) -> None:
    """Text the authoritative splitter reduces to no sentences would persist an empty version
    with no substantive segments, which every gate then lets through. It is refused before
    anything is written, on both the edit path and the no-base (pricing) path; a stale base
    still takes precedence."""
    question, base = _plan_fixture_answer(db_session)
    before = len(events_for(db_session, question.id))
    with pytest.raises(EmptyEdit) as excinfo:
        save_human_edit(db_session, question, " \n\n \t", base.id, ACTOR)
    assert excinfo.value.detail == "The answer text contains no sentences."
    assert current_answer(db_session, question).id == base.id
    versions_ = db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all()
    assert [a.version for a in versions_] == [1]
    assert question.status == "ai_draft"
    assert len(events_for(db_session, question.id)) == before

    with pytest.raises(StaleBaseVersion):
        save_human_edit(db_session, question, "   ", uuid.uuid4(), ACTOR)

    pricing = make_question(db_session, response_type="pricing")
    with pytest.raises(EmptyEdit):
        save_human_edit(db_session, pricing, "\n \n", None, ACTOR)
    assert current_answer(db_session, pricing) is None
    assert pricing.status == "not_started"
    assert events_for(db_session, pricing.id) == []

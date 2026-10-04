# ruff: noqa: F811  (pytest fixtures are imported and then named as parameters)
"""The single-question draft pipeline against a scripted FakeLLM stream.

The scripted NDJSON includes one model segment that must be split, two that must be merged,
a connective sentence, two bad lines, a fact-sourced sentence and a sentence whose only source
is unknown. Asserts: emitted indices equal persisted indices; support events carry offsets that
slice the section to the quote; the persisted version records model and prompt version; a
failing stream persists nothing.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Answer, Event, Message, Question
from app.generate import pipeline
from app.generate.errors import Conflict409
from app.generate.segmentation import join_segments
from app.generate.verbatim import build_verbatim_segments
from app.review.support import summarise_support
from tests.test_generate_fixtures import (
    EXPECTED_TEXTS,
    FACT_STATEMENT,
    GAP,
    QUESTION_TEXT,
    S_A,
    Collector,
    build_library,
    build_question,
    default_synthesis_lines,
    fake_candidate,
    script_entailment,
    script_synthesis,
    stubbed_modules,  # noqa: F401  (fixture)
)


def _events_by_index(events: list[dict], key: str = "index") -> dict[int, dict]:
    return {event[key]: event for event in events}


def test_draft_question_streams_conformed_segments_and_persists_a_version(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    # A question whose text differs from the item's question so no verbatim offer fires.
    question = build_question(
        db_session, text="Explain your approach to clinical risk management under DCB0129."
    )
    stubbed_modules.candidates = [fake_candidate(library.item)]
    stubbed_modules.facts = [library.fact]
    script_synthesis(stubbed_modules.fake_llm, default_synthesis_lines(library), chunks=True)
    script_entailment(stubbed_modules.fake_llm, "supported")
    collector = Collector()

    answer = pipeline.draft_question(
        db_session, question, actor="test user", instruction=None, emit=collector
    )
    db_session.flush()

    # Event order: segments (no verbatim), gaps, fact_checklist, one support per segment.
    segments = collector.of("segment")
    supports = collector.of("support")
    assert collector.types[: len(segments)] == ["segment"] * len(segments)
    assert collector.types[len(segments) : len(segments) + 2] == ["gaps", "fact_checklist"]
    assert collector.types[len(segments) + 2 :] == ["support"] * len(segments)
    assert "verbatim" not in collector.types

    # The split, the merge and the skipped lines produce exactly these sentences, in order.
    assert [segment["text"] for segment in segments] == EXPECTED_TEXTS
    assert [segment["index"] for segment in segments] == list(range(len(EXPECTED_TEXTS)))
    assert all(segment["support_status"] == "pending" for segment in segments)
    assert all(
        source["locator"]["start"] is None and source["locator"]["end"] is None
        for segment in segments
        for source in segment["sources"]
    )
    # Each half of the split segment carries the source; the merged segment carries both.
    assert segments[0]["sources"][0]["source_id"] == str(library.item.id)
    assert segments[1]["sources"][0]["source_id"] == str(library.item.id)
    assert [s["source_type"] for s in segments[2]["sources"]] == ["knowledge_item", "fact"]
    assert segments[2]["paragraph"] == 1 and segments[2]["kind"] == "substantive"
    # Locator and document fields come from the cited rows.
    locator = segments[0]["sources"][0]["locator"]
    assert locator["document_id"] == str(library.document.id)
    assert locator["section_id"] == str(library.section.id)
    assert locator["heading_path"] == ["3", "3.2"] and locator["page"] == 4
    assert segments[0]["sources"][0]["document_title"] == library.document.filename
    assert segments[0]["sources"][0]["doc_type"] == "past_submission"
    assert segments[0]["sources"][0]["effective_date"] == "2025-03-01"
    # The unknown source was dropped.
    assert segments[5]["sources"] == []

    # Emitted indices are the persisted indices.
    assert [segment["index"] for segment in answer.segments] == [s["index"] for s in segments]
    assert [segment["text"] for segment in answer.segments] == EXPECTED_TEXTS

    # Support events carry verified statuses and offsets that slice the section to the quote.
    by_index = _events_by_index(supports)
    assert set(by_index) == set(range(len(EXPECTED_TEXTS)))
    for index in (0, 1, 2, 4):
        assert by_index[index]["support_status"] == "supported", index
    assert by_index[3]["support_status"] == "connective"
    assert by_index[5]["support_status"] == "unsupported"
    for support in supports:
        for source in support["sources"]:
            section = (
                library.section
                if source["source_type"] == "knowledge_item"
                else library.fact_section
            )
            assert source["locator"]["start"] is not None
            assert (
                section.text[source["locator"]["start"] : source["locator"]["end"]]
                == source["quote"]
            )
    assert by_index[0]["sources"][0]["tier"] == "verbatim"
    assert by_index[4]["sources"][0]["quote"] == FACT_STATEMENT
    # The persisted segments are the verified ones, never pending.
    assert [s["support_status"] for s in answer.segments] == [
        by_index[i]["support_status"] for i in range(len(EXPECTED_TEXTS))
    ]
    assert "pending" not in {s["support_status"] for s in answer.segments}

    # The version.
    settings = get_settings()
    assert answer.author_type == "ai" and answer.author_name is None
    assert answer.version == 1 and answer.is_current is True
    assert answer.model == settings.model_main
    assert answer.prompt_version == "synthesis.v2"
    assert answer.text == join_segments(answer.segments)
    assert answer.text.count("\n\n") == 2, "three paragraphs"
    assert answer.word_count == len(answer.text.split())
    assert answer.gaps == [GAP]
    assert answer.fact_checklist == [
        {
            "fact_id": str(library.fact.id),
            "statement": FACT_STATEMENT,
            "effective_date": "2024-03-01",
            "status": "current",
        }
    ]
    assert collector.of("gaps") == [{"type": "gaps", "gaps": [GAP]}]
    assert collector.of("fact_checklist")[0]["fact_checklist"] == answer.fact_checklist
    assert answer.verbatim_offer_item_id is None
    assert answer.support_summary["substantive"] == 5
    assert answer.support_summary["supported"] == 4
    assert answer.support_summary["score"] == 0.8
    assert question.status == "ai_draft"

    events = db_session.scalars(
        select(Event).where(Event.entity_id == question.id, Event.event_type == "answer_created")
    ).all()
    assert len(events) == 1
    assert events[0].actor == "test user"
    assert events[0].payload["answer_id"] == str(answer.id)
    assert events[0].payload["retrieved_item_ids"] == [str(library.item.id)]

    # The synthesis prompt received the candidate's own slice, never the whole section.
    synthesis_call = next(c for c in stubbed_modules.fake_llm.calls if c.name == "synthesis")
    assert library.item.answer_text in synthesis_call.user
    assert "End of section." not in synthesis_call.user
    assert f"[item:{library.item.id}]" in synthesis_call.user
    assert f"[fact:{library.fact.id}]" in synthesis_call.user
    assert "status=current" in synthesis_call.user
    assert "Word limit: 300" in synthesis_call.user
    assert "Buyer: Example NHS Trust" in synthesis_call.user
    assert synthesis_call.model == settings.model_main
    entailment_call = next(c for c in stubbed_modules.fake_llm.calls if c.name == "entailment")
    assert entailment_call.model == settings.model_fast
    assert stubbed_modules.calls[0] == ("retrieve", question.text, ["clinical_safety"])


def test_verbatim_offer_fires_for_a_near_identical_question(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    question = build_question(db_session, text=QUESTION_TEXT)  # identical: cosine 1.0
    stubbed_modules.candidates = [fake_candidate(library.item)]
    script_synthesis(stubbed_modules.fake_llm, default_synthesis_lines(library))
    script_entailment(stubbed_modules.fake_llm)
    collector = Collector()

    answer = pipeline.draft_question(db_session, question, actor="test user", emit=collector)

    assert collector.types[0] == "verbatim"
    verbatim = collector.events[0]
    assert verbatim["source_item_id"] == str(library.item.id)
    assert verbatim["similarity"] >= get_settings().verbatim_threshold
    assert [s["text"] for s in verbatim["segments"]][0] == S_A
    for segment in verbatim["segments"]:
        source = segment["sources"][0]
        assert (
            library.section.text[source["locator"]["start"] : source["locator"]["end"]]
            == segment["text"]
        )
        assert segment["support_status"] == "supported"
    assert answer.verbatim_offer_item_id == library.item.id
    serialised = pipeline.serialise_answer(db_session, answer)
    assert serialised["verbatim"]["source_item_id"] == str(library.item.id)
    assert serialised["verbatim"]["segments"] == verbatim["segments"]


def test_no_verbatim_offer_when_top_candidate_is_a_chunk(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    library.item.item_type = "chunk"
    library.item.question_text = None
    db_session.flush()
    question = build_question(db_session, text=QUESTION_TEXT)
    stubbed_modules.candidates = [fake_candidate(library.item)]
    script_synthesis(stubbed_modules.fake_llm, default_synthesis_lines(library))
    script_entailment(stubbed_modules.fake_llm)
    collector = Collector()
    pipeline.draft_question(db_session, question, actor="test user", emit=collector)
    assert "verbatim" not in collector.types
    synthesis_call = next(c for c in stubbed_modules.fake_llm.calls if c.name == "synthesis")
    assert "Heading path: 3 > 3.2" in synthesis_call.user


def test_pricing_and_displacement_raise_409_before_anything_runs(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    pricing = build_question(db_session, response_type="pricing", number="9.1")
    with pytest.raises(Conflict409) as excinfo:
        pipeline.draft_question(db_session, pricing, actor="test user")
    assert excinfo.value.code == "pricing"

    library = build_library(db_session)
    question = build_question(db_session, status="writer_edited", number="3.3")
    human_segments = [
        {
            "index": 0,
            "paragraph": 0,
            "text": "Human text.",
            "kind": "substantive",
            "sources": [],
            "dispute": None,
            "support_status": "human_authored",
        }
    ]
    current = Answer(
        org_id=question.org_id,
        question_id=question.id,
        version=1,
        author_type="user",
        author_name="Jane",
        text="Human text.",
        word_count=2,
        segments=human_segments,
        support_summary=summarise_support(human_segments),
        is_current=True,
    )
    db_session.add(current)
    db_session.flush()
    stubbed_modules.candidates = [fake_candidate(library.item)]
    with pytest.raises(Conflict409) as excinfo:
        pipeline.draft_question(db_session, question, actor="test user")
    assert excinfo.value.code == "displacement"
    assert excinfo.value.current_answer["id"] == str(current.id)
    assert excinfo.value.body()["current_answer"]["version"] == 1
    assert stubbed_modules.fake_llm.calls == [], "nothing was generated"

    # With the flag the draft displaces the human version, which stays in the history.
    script_synthesis(stubbed_modules.fake_llm, default_synthesis_lines(library))
    script_entailment(stubbed_modules.fake_llm)
    answer = pipeline.draft_question(db_session, question, actor="test user", confirm_displace=True)
    assert answer.version == 2 and answer.is_current
    db_session.refresh(current)
    assert current.is_current is False
    assert question.status == "ai_draft"


def test_failing_stream_persists_nothing(db_session: Session, stubbed_modules) -> None:  # noqa: ANN001
    library = build_library(db_session)
    question = build_question(db_session, text="Something else about clinical safety.")
    stubbed_modules.candidates = [fake_candidate(library.item)]
    lines = default_synthesis_lines(library)

    def broken_stream(**kwargs):  # noqa: ANN001, ANN202
        yield json.dumps(lines[0]) + "\n"
        yield json.dumps(lines[1]) + "\n"
        raise RuntimeError("stream dropped")

    stubbed_modules.fake_llm.register("synthesis", broken_stream)
    db_session.commit()  # the fixtures stay; only the failed run is rolled back
    collector = Collector()
    with pytest.raises(RuntimeError, match="stream dropped"):
        pipeline.draft_question(db_session, question, actor="test user", emit=collector)
    db_session.rollback()
    # Two segments were streamed before the failure; nothing reached the database.
    assert collector.types == ["segment", "segment"]
    assert db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all() == []
    db_session.refresh(question)
    assert question.status == "not_started"


def test_empty_model_output_is_a_draft_error(db_session: Session, stubbed_modules) -> None:  # noqa: ANN001
    library = build_library(db_session)
    question = build_question(db_session, text="Anything.")
    stubbed_modules.candidates = [fake_candidate(library.item)]
    script_synthesis(stubbed_modules.fake_llm, ["not json", {"type": "gaps", "gaps": []}])
    from app.generate.errors import DraftError

    with pytest.raises(DraftError) as excinfo:
        pipeline.draft_question(db_session, question, actor="test user")
    assert excinfo.value.code == "empty_draft"


def test_parse_synthesis_line_skips_bad_lines_and_iterates_chunks() -> None:
    assert pipeline.parse_synthesis_line("") is None
    assert pipeline.parse_synthesis_line("```json") is None
    assert pipeline.parse_synthesis_line("{not json") is None
    assert pipeline.parse_synthesis_line('{"type": "segment"}') is None, "text is required"
    assert pipeline.parse_synthesis_line('{"type": "other", "text": "x"}') is None
    parsed = pipeline.parse_synthesis_line(
        '{"type": "segment", "text": "Hello.", "sources": [{"source_type": "fact", '
        '"source_id": "abc", "quote": "q"}]}'
    )
    assert isinstance(parsed, pipeline.SegmentLine)
    assert parsed.paragraph == 0 and parsed.kind == "substantive"
    assert parsed.sources[0].source_id == "abc"
    gaps = pipeline.parse_synthesis_line('{"type": "gaps", "gaps": ["a", "b"]}')
    assert isinstance(gaps, pipeline.GapsLine) and gaps.gaps == ["a", "b"]

    chunks = ['{"a": 1}\n{"b"', ": 2}\n", '{"c": 3}']
    assert list(pipeline._iter_lines(chunks)) == ['{"a": 1}', '{"b": 2}', '{"c": 3}']


def test_reply_in_thread_first_turn_uses_the_question_and_persists_a_message(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    question = build_question(db_session, text="How do you manage clinical risk?")
    thread = question.thread
    stubbed_modules.candidates = [fake_candidate(library.item)]
    stubbed_modules.facts = [library.fact]
    script_synthesis(stubbed_modules.fake_llm, default_synthesis_lines(library))
    script_entailment(stubbed_modules.fake_llm)
    collector = Collector()

    message = pipeline.reply_in_thread(
        db_session, thread, content="Draft an answer for me.", actor="test user", emit=collector
    )
    db_session.flush()

    stored = db_session.scalars(
        select(Message).where(Message.thread_id == thread.id).order_by(Message.created_at)
    ).all()
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[0].content == "Draft an answer for me."
    assert stored[0].segments is None and stored[0].model is None
    assert stored[1].id == message.id
    assert message.rewritten_query is None, "no history, no rewrite"
    assert not any(c.name == "query_rewrite" for c in stubbed_modules.fake_llm.calls)
    assert message.retrieved_item_ids == [str(library.item.id)]
    assert [s["text"] for s in message.segments] == EXPECTED_TEXTS
    assert message.content == join_segments(message.segments)
    assert message.gaps == [GAP]
    assert message.fact_checklist[0]["fact_id"] == str(library.fact.id)
    assert message.model == get_settings().model_main
    assert message.prompt_version == "synthesis.v2"
    assert message.support_summary["substantive"] == 5
    assert stubbed_modules.calls[0] == ("retrieve", question.text, ["clinical_safety"])
    synthesis_call = next(c for c in stubbed_modules.fake_llm.calls if c.name == "synthesis")
    assert synthesis_call.history == []
    assert "Draft an answer for me." in synthesis_call.user
    serialised = pipeline.serialise_message(db_session, message)
    assert serialised["id"] == str(message.id) and serialised["role"] == "assistant"
    assert serialised["segments"][0]["text"] == S_A
    assert db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all() == []
    assert question.status == "not_started", "a thread reply is not an answer version"


def test_reply_in_thread_with_history_rewrites_the_query(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    question = build_question(db_session, text="How do you manage clinical risk?")
    thread = question.thread
    db_session.add_all(
        [
            Message(org_id=thread.org_id, thread_id=thread.id, role="user", content="Draft it."),
            Message(
                org_id=thread.org_id, thread_id=thread.id, role="assistant", content="A draft."
            ),
        ]
    )
    db_session.flush()
    db_session.refresh(thread)
    stubbed_modules.candidates = [fake_candidate(library.item)]
    stubbed_modules.fake_llm.register(
        "query_rewrite",
        {"query": "How is the clinical safety case for the care record kept current?"},
    )
    script_synthesis(stubbed_modules.fake_llm, default_synthesis_lines(library))
    script_entailment(stubbed_modules.fake_llm)

    message = pipeline.reply_in_thread(
        db_session, thread, content="Make it shorter.", actor="test user"
    )

    rewrite_call = next(c for c in stubbed_modules.fake_llm.calls if c.name == "query_rewrite")
    assert rewrite_call.model == get_settings().model_main
    assert "Latest user turn:\nMake it shorter." in rewrite_call.user
    assert question.text in rewrite_call.user
    assert (
        message.rewritten_query
        == "How is the clinical safety case for the care record kept current?"
    )
    assert stubbed_modules.calls[0][1] == message.rewritten_query
    synthesis_call = next(c for c in stubbed_modules.fake_llm.calls if c.name == "synthesis")
    assert synthesis_call.history == [
        {"role": "user", "content": "Draft it."},
        {"role": "assistant", "content": "A draft."},
    ]


def test_llm_history_merges_consecutive_roles_and_starts_with_user() -> None:
    def m(role: str, content: str) -> Message:
        return Message(org_id=uuid.uuid4(), thread_id=uuid.uuid4(), role=role, content=content)

    history = pipeline.llm_history(
        [
            m("assistant", "dropped"),
            m("user", "a"),
            m("user", "b"),
            m("system", "x"),
            m("assistant", "c"),
        ]
    )
    assert history == [
        {"role": "user", "content": "a\n\nb"},
        {"role": "assistant", "content": "c"},
    ]


def test_check_displacement_only_past_ai_draft(db_session: Session) -> None:
    for status in ("not_started", "ai_draft"):
        question = build_question(db_session, status=status, number=f"n-{status}")
        pipeline.check_displacement(db_session, question, confirm_displace=False)
    for status in ("writer_edited", "sme_verified", "approved"):
        question = build_question(db_session, status=status, number=f"p-{status}")
        with pytest.raises(Conflict409):
            pipeline.check_displacement(db_session, question, confirm_displace=False)
        pipeline.check_displacement(db_session, question, confirm_displace=True)


def test_fact_checklist_unions_listed_and_cited_facts(db_session: Session, stubbed_modules) -> None:  # noqa: ANN001
    library = build_library(db_session, fact_expires_on=date(2020, 1, 1))
    question = build_question(db_session, text="Who is your clinical safety officer?")
    stubbed_modules.candidates = [fake_candidate(library.item)]
    stubbed_modules.facts = [library.fact]
    lines = default_synthesis_lines(library)
    lines[-1] = {"type": "fact_checklist", "fact_ids": []}  # the model forgot to list it
    script_synthesis(stubbed_modules.fake_llm, lines)
    script_entailment(stubbed_modules.fake_llm, "supported")
    answer = pipeline.draft_question(db_session, question, actor="test user")
    assert [entry["fact_id"] for entry in answer.fact_checklist] == [str(library.fact.id)]
    assert answer.fact_checklist[0]["status"] == "expired"
    # A sentence resting on an expired fact never comes back supported.
    assert answer.segments[4]["support_status"] == "weak"


def test_whole_output_json_fallback_when_no_ndjson_lines(
    db_session: Session, stubbed_modules
) -> None:  # noqa: ANN001
    """A model that pretty-prints one JSON object (no valid NDJSON line) still yields a draft."""
    library = build_library(db_session)
    question = build_question(db_session, text="Anything.")
    stubbed_modules.candidates = [fake_candidate(library.item)]
    lines = [line for line in default_synthesis_lines(library) if isinstance(line, dict)]
    segments = [
        {key: value for key, value in line.items() if key != "type"}
        for line in lines
        if line["type"] == "segment"
    ]
    gaps = next(line["gaps"] for line in lines if line["type"] == "gaps")
    fact_ids = next(line["fact_ids"] for line in lines if line["type"] == "fact_checklist")
    document = {"segments": segments, "gaps": gaps, "fact_checklist": fact_ids}
    pretty = "```json\n" + json.dumps(document, indent=2) + "\n```\n"
    stubbed_modules.fake_llm.register("synthesis", pretty)
    script_entailment(stubbed_modules.fake_llm)

    collector = Collector()
    answer = pipeline.draft_question(db_session, question, actor="test user", emit=collector)
    assert [segment["text"] for segment in answer.segments] == EXPECTED_TEXTS
    assert answer.gaps == [GAP]
    assert collector.types.count("segment") == len(EXPECTED_TEXTS)
    assert collector.types[-1] == "support"


def test_whole_output_array_fallback(db_session: Session, stubbed_modules) -> None:  # noqa: ANN001
    library = build_library(db_session)
    question = build_question(db_session, text="Anything.")
    stubbed_modules.candidates = [fake_candidate(library.item)]
    lines = [line for line in default_synthesis_lines(library) if isinstance(line, dict)]
    stubbed_modules.fake_llm.register("synthesis", json.dumps(lines, indent=1))
    script_entailment(stubbed_modules.fake_llm)

    answer = pipeline.draft_question(db_session, question, actor="test user")
    assert [segment["text"] for segment in answer.segments] == EXPECTED_TEXTS


def test_parse_synthesis_document_shapes() -> None:
    assert pipeline.parse_synthesis_document("") == []
    assert pipeline.parse_synthesis_document("not json at all") == []
    assert pipeline.parse_synthesis_document('"a string"') == []
    parsed = pipeline.parse_synthesis_document(
        json.dumps(
            {
                "segments": [{"text": "Hello.", "sources": []}, {"nonsense": True}],
                "gaps": ["g"],
                "fact_checklist": [{"fact_id": "f1", "statement": "s"}, "f2"],
            }
        )
    )
    assert [type(line).__name__ for line in parsed] == [
        "SegmentLine", "GapsLine", "FactChecklistLine",
    ]
    assert parsed[2].fact_ids == ["f1", "f2"]
    fenced = pipeline.parse_synthesis_document("```\n[{\"type\": \"gaps\", \"gaps\": []}]\n```")
    assert len(fenced) == 1 and isinstance(fenced[0], pipeline.GapsLine)


def test_persist_ai_answer_re_reads_the_question_before_displacing(
    db_session: Session,
    stubbed_modules,  # noqa: ANN001
) -> None:
    """A card draft loads the question, then spends seconds in synthesis. A human edit saved
    and committed meanwhile must not be displaced on the strength of the stale ``status``:
    the row is re-read under a lock at persist time and the draft is refused."""
    library = build_library(db_session)
    question = build_question(db_session, status="ai_draft")
    db_session.flush()

    # The concurrent writer: another session on the same connection commits a human version
    # and moves the question to writer_edited while our in-memory row still says ai_draft.
    other = Session(
        bind=db_session.bind, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        human = Answer(
            org_id=question.org_id,
            question_id=question.id,
            version=1,
            author_type="user",
            author_name="editor",
            text="A human rewrite.",
            word_count=3,
            segments=[],
            gaps=[],
            fact_checklist=[],
            support_summary=summarise_support([]),
            is_current=True,
        )
        other.add(human)
        other.execute(
            update(Question).where(Question.id == question.id).values(status="writer_edited")
        )
        other.commit()
        human_id = human.id
    finally:
        other.close()
    assert question.status == "ai_draft", "the draft's own copy of the row is stale"

    segments = build_verbatim_segments(db_session, library.item)
    with pytest.raises(Conflict409) as excinfo:
        pipeline.persist_ai_answer(
            db_session,
            question,
            actor="test user",
            confirm_displace=False,
            segments=segments,
            gaps=[],
            fact_checklist=[],
            model="fake-model",
            prompt_version="synthesis.v2",
        )
    assert excinfo.value.code == "displacement"
    assert excinfo.value.current_answer["id"] == str(human_id)
    assert question.status == "writer_edited", "the refresh brought the committed row in"
    answers = db_session.scalars(select(Answer).where(Answer.question_id == question.id)).all()
    assert [a.id for a in answers] == [human_id] and answers[0].is_current
    assert (
        db_session.scalars(
            select(Event).where(
                Event.entity_id == question.id, Event.event_type == "answer_created"
            )
        ).all()
        == []
    )

    # With the confirmation the write proceeds against the fresh row.
    answer = pipeline.persist_ai_answer(
        db_session,
        question,
        actor="test user",
        confirm_displace=True,
        segments=segments,
        gaps=[],
        fact_checklist=[],
        model="fake-model",
        prompt_version="synthesis.v2",
    )
    assert answer.is_current and answer.version == 2
    db_session.refresh(human := db_session.get(Answer, human_id))
    assert human.is_current is False


def test_fact_blocks_label_facts_that_are_not_current() -> None:
    """Facts attached to a retrieved item reach the prompt whatever their state; each block
    says whether the model may rely on it (draft step 5, synthesis rule on stale facts)."""
    import uuid as _uuid
    from datetime import date as _date

    from app.db.models import Document, Fact
    from app.generate.pipeline import FactContext, build_synthesis_user_message

    def context(**fact_fields: object) -> FactContext:
        fact = Fact(
            id=_uuid.uuid4(), fact_kind="iso_27001", value="IS 12345", statement="We hold ISO.",
            effective_date=_date(2024, 1, 1), **fact_fields,
        )
        document = Document(filename="iso.docx")
        return FactContext(fact=fact, section=None, document=document)

    current = context()
    superseded = context(superseded_by=_uuid.uuid4())
    expired = context(expires_on=_date(2020, 1, 1))
    from_superseded_document = context()
    from_superseded_document.document.superseded_by = _uuid.uuid4()

    message = build_synthesis_user_message(
        query_text="Do you hold ISO 27001?", response_type="free_text", word_limit=None,
        buyer=None, instruction=None, candidates=[],
        facts=[current, superseded, expired, from_superseded_document],
    )
    blocks = message.split("## Facts\n", 1)[1].split("\n\n")
    assert "status=current" in blocks[0]
    assert "status=not_current reason=superseded" in blocks[1]
    assert "status=not_current reason=expired" in blocks[2]
    assert "status=not_current reason=document_superseded" in blocks[3]

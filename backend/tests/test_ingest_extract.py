"""Step 3: anchor location and disambiguation, the fidelity check, the find_span fallback,
fragments, overlap collapse, and the full extract_pairs flow over a generated docx."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import TABLE_CELL_DELIMITER, get_settings
from app.db.models import KnowledgeItem, UnpairedFragment
from app.ingest.extract import (
    PROMPT_NAME,
    build_windows,
    clean_topics,
    extract_pairs,
    locate_by_anchors,
    locate_text,
    to_raw_facts,
)
from app.ingest.types import ExtractedFact, PairExtractionOutput
from tests.test_ingest_builders import (
    A11_LINES,
    A12_LINES,
    A21,
    A22,
    A31,
    A32,
    Q11,
    Q12,
    Q21,
    Q22,
    Q31,
    Q32,
    Q33,
    anchors,
    build_submission_docx,
    make_document,
    parse_and_persist,
    section_containing,
    section_starting,
)

# --- Anchor location ----------------------------------------------------------------------------

REPEATED = "We ensure safety. We ensure safety by testing every release thoroughly."


def test_start_anchor_takes_first_match_after_previous_pair() -> None:
    copied = "We ensure safety by testing every release thoroughly."
    start, end = anchors(copied, 3)
    # From the top, the first "We ensure safety" makes a slice far longer than the copy.
    assert locate_by_anchors(REPEATED, start, end, copied) is None
    # After the previous pair's end, the second occurrence is taken and the lengths agree.
    span = locate_by_anchors(REPEATED, start, end, copied, search_from=len("We ensure safety. "))
    assert span is not None
    assert REPEATED[span[0] : span[1]] == copied


def test_end_anchor_takes_nearest_match_after_start() -> None:
    text = "Alpha beta gamma. Alpha beta gamma delta."
    span = locate_by_anchors(text, "Alpha beta", "gamma.", "Alpha beta gamma.")
    assert span is not None and text[span[0] : span[1]] == "Alpha beta gamma."


def test_length_sanity_check_rejects_a_slice_a_quarter_off() -> None:
    text = "Start here and then a very long digression of many words follows before the end."
    copied = "Start here before the end."
    assert locate_by_anchors(text, "Start here", "the end.", copied) is None


def test_normalisation_makes_curly_quotes_and_whitespace_irrelevant() -> None:
    text = "Heading\nWe   use “secure” tokens —\nalways. Then more."
    copied = 'We use "secure" tokens - always.'
    span = locate_by_anchors(text, 'We use "secure"', "tokens - always.", copied)
    assert span is not None
    assert text[span[0] : span[1]] == "We   use “secure” tokens —\nalways."


def test_answer_outside_named_section_falls_back_to_find_span_then_unverified() -> None:
    section = "4.1 Question line\nThe answer starts here and ends in this section."
    following = "4.1.1 Sub-heading\nBut the model thought it continued here."
    copied = "The answer starts here and ends in this section."
    # End anchor from the following section: anchors fail, find_span on the copy succeeds.
    located = locate_text(section, "The answer starts", "it continued here.", copied)
    assert located is not None and located.by_anchors is False
    assert section[located.start : located.end] == copied
    # A copy spanning two sections is found in neither.
    spanning = copied + " " + following
    assert locate_text(section, "The answer starts", "continued here.", spanning) is None
    assert locate_text(section, "Nothing", "here", "Completely different text altogether.") is None


def test_anchor_located_text_reports_by_anchors() -> None:
    copied = A21
    start, end = anchors(copied)
    row = f"{Q21}{TABLE_CELL_DELIMITER}{A21}"
    located = locate_text(row, start, end, copied)
    assert located is not None and located.by_anchors is True
    assert row[located.start : located.end] == A21


# --- Helpers ------------------------------------------------------------------------------------


class _Section:
    def __init__(self, text: str) -> None:
        self.text = text


def test_build_windows_overlap_by_one_section() -> None:
    sections = [_Section("x" * 400) for _ in range(6)]  # ~130 tokens each with label
    windows = build_windows(sections, window_tokens=300)
    assert len(windows) > 1
    for previous, following in zip(windows, windows[1:], strict=False):
        assert following[0] is previous[-1]
    seen = {id(s) for window in windows for s in window}
    assert len(seen) == 6
    assert build_windows([], window_tokens=300) == []


def test_clean_topics_and_raw_facts_validation() -> None:
    taxonomy = ["clinical_safety", "information_security"]
    given = ["information_security", "bogus", "clinical_safety", "clinical_safety"]
    assert clean_topics(given, taxonomy) == ["information_security", "clinical_safety"]
    import uuid

    section_id = uuid.uuid4()
    facts = to_raw_facts(
        [
            ExtractedFact(fact_kind="iso_27001", fact_key="ignored", statement="S.", value="123"),
            ExtractedFact(fact_kind="dspt_status", fact_key="X26", statement="T.", value="Met"),
            ExtractedFact(fact_kind="nonsense", statement="U.", value="1"),
            ExtractedFact(fact_kind="headcount", statement="", value="42"),
        ],
        section_id=section_id,
        knowledge_item_id=None,
    )
    kept = [(f.fact_kind, f.fact_key) for f in facts]
    assert kept == [("iso_27001", None), ("dspt_status", "X26")]
    assert all(f.section_id == section_id for f in facts)


# --- Full flow ----------------------------------------------------------------------------------


def _pair(q_section, a_section, question: str, answer: str, **extra):
    q_start, q_end = anchors(question)
    a_start, a_end = anchors(answer)
    return {
        "question_section_id": str(q_section.id),
        "answer_section_id": str(a_section.id),
        "question_start_anchor": q_start,
        "question_end_anchor": q_end,
        "answer_start_anchor": a_start,
        "answer_end_anchor": a_end,
        "question_text_copy": question,
        "answer_text_copy": answer,
        "topics": extra.pop("topics", ["clinical_safety"]),
        "facts": extra.pop("facts", []),
    }


def _scripted_output(sections) -> dict:
    s11 = section_starting(sections, Q11)
    s12 = section_starting(sections, Q12)
    row21 = section_starting(sections, Q21)
    row22 = section_starting(sections, Q22)
    prose3 = section_starting(sections, "3. Implementation")
    box31 = section_containing(sections, A31)
    box32 = section_containing(sections, A32)
    a11 = "\n".join(A11_LINES)
    return {
        "pairs": [
            # Layout 2: heading question, answer beneath, same section.
            _pair(
                s11,
                s11,
                Q11,
                a11,
                facts=[
                    {
                        "fact_kind": "clinical_safety_officer",
                        "fact_key": None,
                        "statement": A11_LINES[1],
                        "value": "Dr Amira Patel",
                    },
                    {
                        "fact_kind": "clinical_safety_case",
                        "fact_key": "Scribe",
                        "statement": A11_LINES[2],
                        "value": "released",
                        "effective_date": "2025-02-02",
                    },
                ],
            ),
            # Paraphrased copy with wrong anchors: fidelity check fails, stored unverified.
            {
                **_pair(s12, s12, Q12, " ".join(A12_LINES)),
                "answer_start_anchor": "Hazards get written down",
                "answer_end_anchor": "with the customer.",
                "answer_text_copy": (
                    "Hazards get written down in a log and scored with the customer."
                ),
            },
            # Layout 1: adjacent cells, same row section.
            _pair(
                row21,
                row21,
                Q21,
                A21,
                topics=["information_security", "not_in_taxonomy"],
                facts=[
                    {
                        "fact_kind": "dspt_status",
                        "fact_key": "X26",
                        "statement": A21.split(". ")[0] + ".",
                        "value": "Standards Met",
                        "effective_date": "2025-06-28",
                    }
                ],
            ),
            _pair(
                row22,
                row22,
                Q22,
                A22,
                topics=["information_security"],
                facts=[
                    {
                        "fact_kind": "cyber_essentials_plus",
                        "statement": A22.split(". ")[0] + ".",
                        "value": "IASME-CEP-004211",
                        "effective_date": "2025-01-12",
                        "expires_on": "2026-01-11",
                    }
                ],
            ),
            # Layout 3: question in prose, answer in a boxed row.
            _pair(prose3, box31, Q31, A31, topics=["implementation_and_onboarding"]),
            _pair(prose3, box32, Q32, A32, topics=["training_and_support"]),
        ],
        "fragments": [
            {"section_id": str(prose3.id), "role": "question", "text": Q33},
            {"section_id": str(prose3.id), "role": "question", "text": Q33},  # duplicate
        ],
    }


@pytest.fixture
def submission(db_session: Session, tmp_path: Path):
    path = build_submission_docx(tmp_path / "submission.docx")
    document = make_document(
        db_session, path, doc_type="past_submission", ingest_status="extracting"
    )
    sections = parse_and_persist(db_session, document)
    return document, sections


def test_extract_pairs_locates_anchors_and_records_fidelity(
    db_session: Session, fake_llm, submission
) -> None:
    document, sections = submission
    fake_llm.register(PROMPT_NAME, _scripted_output(sections))

    result = extract_pairs(db_session, document, sections)

    assert len(result.items) == 6
    by_question = {item.question_text: item for item in result.items}
    assert set(by_question) == {Q11, Q12, Q21, Q22, Q31, Q32}

    # Same-section pairs store a null question_section_id; slices come from the section text.
    s11 = section_starting(sections, Q11)
    item11 = by_question[Q11]
    assert item11.item_type == "qa_pair" and item11.text_verified is True
    assert item11.section_id == s11.id and item11.question_section_id is None
    assert s11.text[item11.answer_start : item11.answer_end] == item11.answer_text
    assert item11.answer_text == "\n".join(A11_LINES)
    assert s11.text[item11.question_start : item11.question_end] == Q11
    assert item11.topics == ["clinical_safety"]

    # Adjacent-cells row: the answer slice is the response cell only.
    row21 = section_starting(sections, Q21)
    item21 = by_question[Q21]
    assert item21.text_verified and row21.text[item21.answer_start : item21.answer_end] == A21
    assert item21.topics == ["information_security"], "topics outside the taxonomy are dropped"

    # Boxed layout: question and answer in different sections.
    prose3 = section_starting(sections, "3. Implementation")
    box31 = section_containing(sections, A31)
    item31 = by_question[Q31]
    assert item31.section_id == box31.id and item31.question_section_id == prose3.id
    assert box31.text[item31.answer_start : item31.answer_end] == A31
    assert prose3.text[item31.question_start : item31.question_end] == Q31
    item32 = by_question[Q32]
    assert prose3.text[item32.question_start : item32.question_end] == Q32

    # The paraphrased pair failed the fidelity check and the fallback: stored, unverified.
    item12 = by_question[Q12]
    assert item12.text_verified is False
    assert item12.answer_text.startswith("Hazards get written down")
    assert (item12.answer_start, item12.answer_end) == (0, 0)

    # Facts reference the answer's section and item; keys follow the kind's rule.
    facts = {(f.fact_kind, f.fact_key): f for f in result.raw_facts}
    assert set(facts) == {
        ("clinical_safety_officer", None),
        ("clinical_safety_case", "Scribe"),
        ("dspt_status", "X26"),
        ("cyber_essentials_plus", None),
    }
    ce = facts[("cyber_essentials_plus", None)]
    assert ce.section_id == section_starting(sections, Q22).id
    assert ce.knowledge_item_id == by_question[Q22].id
    assert ce.effective_date == date(2025, 1, 12) and ce.expires_on == date(2026, 1, 11)
    assert facts[("clinical_safety_officer", None)].effective_date is None

    # The unpaired question is one fragment, located in its section, duplicates collapsed.
    fragments = db_session.scalars(
        select(UnpairedFragment).where(UnpairedFragment.document_id == document.id)
    ).all()
    assert len(fragments) == 1 == len(result.fragments)
    fragment = fragments[0]
    assert fragment.role == "question" and fragment.section_id == prose3.id
    assert prose3.text[fragment.start : fragment.end] == Q33
    assert fragment.resolved_item_id is None

    stored = db_session.scalars(
        select(KnowledgeItem).where(KnowledgeItem.document_id == document.id)
    ).all()
    assert len(stored) == 6
    verified_share = sum(i.text_verified for i in stored) / len(stored)
    assert verified_share >= 5 / 6

    call = fake_llm.calls[-1]
    assert call.name == PROMPT_NAME and call.model == get_settings().model_main
    assert f"section id={s11.id}" in call.user
    assert "key rule" in call.user and "`clinical_safety`" in call.user
    assert "adjacent table cells" in call.system


def test_overlap_duplicates_collapse_to_one_item(
    db_session: Session, fake_llm, settings_override, submission
) -> None:
    document, sections = submission
    settings_override(pair_extraction_window_tokens=120)
    scripted = PairExtractionOutput.model_validate(_scripted_output(sections))

    def respond(user: str, **_: object) -> PairExtractionOutput:
        # Return only the pairs whose answer section is labelled in this window's prompt (a
        # boxed answer's question may sit in an earlier window; the server still finds it).
        present = {sid for sid in (str(s.id) for s in sections) if f"section id={sid}" in user}
        return PairExtractionOutput(
            pairs=[p for p in scripted.pairs if p.answer_section_id in present],
            fragments=[f for f in scripted.fragments if f.section_id in present],
        )

    fake_llm.register(PROMPT_NAME, respond)
    windows = build_windows(sections)
    assert len(windows) > 1

    result = extract_pairs(db_session, document, sections)

    assert len([c for c in fake_llm.calls if c.name == PROMPT_NAME]) == len(windows)
    questions = sorted(item.question_text for item in result.items)
    assert questions == sorted([Q11, Q12, Q21, Q22, Q31, Q32]), "no duplicates across windows"
    assert len(result.fragments) == 1


def test_a_failed_window_fails_the_stage_after_every_window_was_attempted(
    db_session: Session, fake_llm, settings_override, submission
) -> None:
    from app.ingest.extract import ExtractionFailed

    document, sections = submission
    settings_override(pair_extraction_window_tokens=120)
    scripted = PairExtractionOutput.model_validate(_scripted_output(sections))
    windows = build_windows(sections)
    assert len(windows) > 1
    first_window_ids = {str(s.id) for s in windows[0]}
    calls: list[int] = []

    def respond(user: str, **_: object) -> PairExtractionOutput:
        present = {sid for sid in (str(s.id) for s in sections) if f"section id={sid}" in user}
        calls.append(len(present))
        if present == first_window_ids:
            raise RuntimeError("model overloaded")
        return PairExtractionOutput(
            pairs=[p for p in scripted.pairs if p.answer_section_id in present],
            fragments=[f for f in scripted.fragments if f.section_id in present],
        )

    fake_llm.register(PROMPT_NAME, respond)
    with pytest.raises(ExtractionFailed) as info:
        extract_pairs(db_session, document, sections)

    assert len(calls) == len(windows), "every window was still attempted"
    message = str(info.value)
    assert f"1 of {len(windows)} window(s)" in message
    assert "window(s) 1;" in message, "the failed window is named"
    assert all(sid in message for sid in first_window_ids), "and so are its sections"
    assert "RuntimeError: model overloaded" in message
    # The stage raised, so the job handler propagates it: no document reaches ready with the
    # pairs of a failed window missing, and the worker's retry rule applies.


def test_every_window_failing_fails_the_stage(
    db_session: Session, fake_llm, submission
) -> None:
    from app.ingest.extract import ExtractionFailed

    document, sections = submission

    def respond(**_: object) -> PairExtractionOutput:
        raise RuntimeError("model overloaded")

    fake_llm.register(PROMPT_NAME, respond)
    windows = build_windows(sections)
    with pytest.raises(
        ExtractionFailed, match=rf"{len(windows)} of {len(windows)} window\(s\)"
    ):
        extract_pairs(db_session, document, sections)

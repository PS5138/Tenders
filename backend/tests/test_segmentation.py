from __future__ import annotations

from app.generate.segmentation import (
    Conformer,
    conform,
    is_sentence_terminal,
    join_segments,
    split_sentences,
    union_sources,
)

S1 = {"source_type": "knowledge_item", "source_id": "item-1", "quote": "first"}
S2 = {"source_type": "fact", "source_id": "fact-1", "quote": "second"}


def test_split_sentences_handles_abbreviations_and_offsets() -> None:
    text = (
        "We use open standards, e.g. FHIR and MESH. Dr. Okafor approves approx. 40 hazards "
        "per year. Is that enough? Yes."
    )
    sentences = split_sentences(text)
    assert [s.text for s in sentences] == [
        "We use open standards, e.g. FHIR and MESH.",
        "Dr. Okafor approves approx. 40 hazards per year.",
        "Is that enough?",
        "Yes.",
    ]
    assert all(text[s.start : s.end] == s.text for s in sentences)
    assert {s.paragraph for s in sentences} == {0}


def test_split_sentences_paragraphs_and_list_items() -> None:
    text = (
        "Our approach:\n\n1. Parse the pack.\n2. Triage every question. Then draft.\n"
        "- Review\n\nDone."
    )
    sentences = split_sentences(text)
    assert [(s.text, s.paragraph, s.is_list_item) for s in sentences] == [
        ("Our approach:", 0, False),
        ("1. Parse the pack.", 1, True),
        ("2. Triage every question.", 1, True),
        ("Then draft.", 1, False),
        ("- Review", 1, True),
        ("Done.", 2, False),
    ]
    assert all(text[s.start : s.end] == s.text for s in sentences)


def test_split_sentences_keeps_decimals_identifiers_and_quotes() -> None:
    text = 'Version 2.5 of DCB0129 applies. He said "we comply." Section 4.2 follows.'
    assert [s.text for s in split_sentences(text)] == [
        "Version 2.5 of DCB0129 applies.",
        'He said "we comply."',
        "Section 4.2 follows.",
    ]


def test_is_sentence_terminal() -> None:
    assert is_sentence_terminal("It works.")
    assert is_sentence_terminal('He said "yes."')
    assert is_sentence_terminal("Founded in 1999.")
    assert is_sentence_terminal("Is that enough?")
    assert not is_sentence_terminal("We integrate with EMIS Web and")
    assert not is_sentence_terminal("We use open standards, e.g.")
    assert not is_sentence_terminal("Approved by Dr.")
    assert not is_sentence_terminal("1.")
    assert not is_sentence_terminal("a)")
    assert not is_sentence_terminal("The stages are: 1.")
    assert not is_sentence_terminal("")


def test_conform_splits_one_model_segment_into_two_each_carrying_the_sources() -> None:
    segments = conform(
        [
            {
                "text": "The first sentence is here. The second sentence follows it.",
                "paragraph": 0,
                "kind": "substantive",
                "sources": [S1],
            }
        ]
    )
    assert [s.text for s in segments] == [
        "The first sentence is here.",
        "The second sentence follows it.",
    ]
    assert [s.index for s in segments] == [0, 1]
    assert segments[0].sources == [S1] and segments[1].sources == [S1]
    assert segments[0].sources[0] is not segments[1].sources[0], "copies, not aliases"
    assert all(s.support_status == "pending" for s in segments)


def test_conform_merges_a_non_terminal_fragment_with_the_next_segment() -> None:
    conformer = Conformer()
    assert (
        conformer.feed(
            {
                "text": "The platform integrates with EMIS Web and",
                "paragraph": 0,
                "kind": "connective",
                "sources": [S1],
            }
        )
        == []
    )
    assert conformer.has_pending
    merged = conformer.feed(
        {
            "text": "SystmOne via IM1 pairing.",
            "paragraph": 0,
            "kind": "substantive",
            "sources": [S2, S1],
        }
    )
    assert len(merged) == 1
    segment = merged[0]
    assert segment.text == "The platform integrates with EMIS Web and SystmOne via IM1 pairing."
    assert segment.sources == [S1, S2], "union in order, de-duplicated"
    assert segment.kind == "substantive", "substantive if any contributor is"
    assert segment.paragraph == 0, "paragraph of the first contributor"
    assert segment.index == 0
    assert conformer.flush() == []


def test_conform_flushes_held_fragment_on_paragraph_change_and_at_end() -> None:
    conformer = Conformer()
    assert conformer.feed({"text": "A heading without a full stop", "paragraph": 0}) == []
    emitted = conformer.feed({"text": "Body sentence.", "paragraph": 1, "sources": [S1]})
    assert [(s.text, s.paragraph, s.index) for s in emitted] == [
        ("A heading without a full stop", 0, 0),
        ("Body sentence.", 1, 1),
    ]
    assert emitted[0].sources == []
    assert conformer.feed({"text": "Trailing fragment", "paragraph": 1}) == []
    flushed = conformer.flush()
    assert [(s.text, s.index) for s in flushed] == [("Trailing fragment", 2)]
    assert conformer.flush() == []


def test_conform_indices_equal_persisted_indices() -> None:
    model_segments = [
        {"text": "One. Two.", "paragraph": 0, "sources": [S1]},
        {"text": "Three and", "paragraph": 0, "sources": [S2]},
        {"text": "four.", "paragraph": 0, "sources": [S1]},
        {"text": "Five", "paragraph": 0},
        {"text": "Six.", "paragraph": 1},
        {"text": "Seven", "paragraph": 1},
    ]
    segments = conform(model_segments)
    assert [s.text for s in segments] == [
        "One.", "Two.", "Three and four.", "Five", "Six.", "Seven",
    ]
    assert [s.index for s in segments] == list(range(len(segments)))
    assert segments[2].sources == [S2, S1]
    assert join_segments(segments) == "One. Two. Three and four. Five\n\nSix. Seven"


def test_conform_ignores_empty_segments_and_accepts_objects() -> None:
    class Model:
        text = "Object based."
        paragraph = 3
        kind = "substantive"
        sources = [S1]

    segments = conform([{"text": "   "}, Model()])
    assert [(s.text, s.paragraph) for s in segments] == [("Object based.", 3)]


def test_union_sources_dedupes_by_type_and_id() -> None:
    attestation = {"source_type": "human_attestation", "attested_by": "Jane", "at": "t"}
    merged = union_sources([S1, attestation], [S1, S2, attestation])
    assert merged == [S1, attestation, S2]

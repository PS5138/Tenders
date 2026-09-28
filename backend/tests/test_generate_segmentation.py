"""Source attribution when a held fragment is merged with the next model segment.

The plan's union rule applies to "a segment formed from more than one model segment". The
other sentences of the combined text came from exactly one model segment and keep only that
segment's sources, so a source is never carried onto a sentence it did not support.
"""

from __future__ import annotations

from app.generate.segmentation import Conformer, conform

A = {"source_type": "knowledge_item", "source_id": "item-a", "quote": "ISO 27001 since 2019"}
B = {"source_type": "knowledge_item", "source_id": "item-b", "quote": "expires in 2027"}


def test_union_applies_only_to_the_sentence_the_held_fragment_joins() -> None:
    conformer = Conformer()
    assert (
        conformer.feed({"text": "We have held ISO 27001 since", "paragraph": 0, "sources": [A]})
        == []
    )
    emitted = conformer.feed(
        {
            "text": "2019. Our Cyber Essentials Plus certificate expires in 2027.",
            "paragraph": 0,
            "sources": [B],
        }
    )
    assert [s.text for s in emitted] == [
        "We have held ISO 27001 since 2019.",
        "Our Cyber Essentials Plus certificate expires in 2027.",
    ]
    assert emitted[0].sources == [A, B], "formed from two model segments: the union"
    assert emitted[1].sources == [B], "formed from the second segment alone: its sources only"
    assert [s.index for s in emitted] == [0, 1]
    assert conformer.flush() == []


def test_sentence_ending_exactly_at_the_held_text_keeps_only_the_held_sources() -> None:
    """The splitter ends a sentence at the fragment's last character: ``"The stages are: 1."``
    is held (an incomplete list item), and the next segment starts a new sentence."""
    segments = conform(
        [
            {"text": "The stages are: 1.", "paragraph": 0, "kind": "connective", "sources": [A]},
            {"text": "Discovery. Then design.", "paragraph": 0, "sources": [B]},
        ]
    )
    assert [s.text for s in segments] == ["The stages are: 1.", "Discovery.", "Then design."]
    assert segments[0].sources == [A] and segments[0].kind == "connective"
    assert segments[1].sources == [B] and segments[1].kind == "substantive"
    assert segments[2].sources == [B]


def test_new_held_remainder_carries_only_its_own_segment_sources_and_kind() -> None:
    conformer = Conformer()
    assert conformer.feed({"text": "Alpha and", "paragraph": 0, "sources": [A]}) == []
    emitted = conformer.feed(
        {"text": "beta. Gamma and", "paragraph": 0, "kind": "connective", "sources": [B]}
    )
    assert [(s.text, s.sources, s.kind) for s in emitted] == [
        ("Alpha and beta.", [A, B], "substantive"),
    ]
    assert conformer.has_pending
    flushed = conformer.flush()
    assert [(s.text, s.sources, s.kind, s.paragraph) for s in flushed] == [
        ("Gamma and", [B], "connective", 0),
    ]
    assert [s.index for s in emitted + flushed] == [0, 1]


def test_merged_sentence_takes_the_held_paragraph_and_the_rest_their_own() -> None:
    """A held fragment merges only when the next paragraph is not greater; a lower paragraph
    from the model still attributes each sentence to the segment its text came from."""
    segments = conform(
        [
            {"text": "Held in paragraph two and", "paragraph": 2, "sources": [A]},
            {"text": "finished here. A separate sentence.", "paragraph": 1, "sources": [B]},
        ]
    )
    assert [(s.text, s.paragraph, s.sources) for s in segments] == [
        ("Held in paragraph two and finished here.", 2, [A, B]),
        ("A separate sentence.", 1, [B]),
    ]

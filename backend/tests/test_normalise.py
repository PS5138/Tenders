from __future__ import annotations

import pytest

from app.ingest.normalise import find_span, normalise, normalised_equal


def test_normalise_maps_quotes_dashes_spaces_and_case() -> None:
    original = "The  “Meridian  Care Record” – ISO 27001\n\nCertified."
    normalised, index = normalise(original)
    assert normalised == 'the "meridian care record" - iso 27001 certified.'
    assert len(index) == len(normalised)
    assert all(0 <= position < len(original) for position in index)
    # The ASCII quote maps back to the curly quote that produced it.
    assert original[index[normalised.index('"')]] == "“"
    # A collapsed whitespace run maps to its first character.
    assert original[index[normalised.index(" iso") ]] == " "


def test_normalise_joins_hyphenated_line_breaks_and_drops_soft_hyphens() -> None:
    original = "infor-\nmation govern­ance"
    normalised, index = normalise(original)
    assert normalised == "information governance"
    # 'm' of "mation" points at its original position after the line break.
    assert original[index[normalised.index("m")]] == "m"
    assert index[normalised.index("m")] == original.index("m")


def test_normalise_strips_and_handles_empty() -> None:
    assert normalise("   ") == ("", [])
    assert normalise("  Hello \t") == ("hello", [2, 3, 4, 5, 6])


def test_find_span_verbatim_tier_returns_original_offsets() -> None:
    text = (
        "Our Clinical Safety Officer, Dr Amara Okafor, reviews the “hazard log” "
        "quarterly."
    )
    match = find_span('dr amara  okafor, reviews the "hazard log"', text)
    assert match is not None
    assert match.tier == "verbatim"
    assert match.ratio == 1.0
    assert text[match.start : match.end] == "Dr Amara Okafor, reviews the “hazard log”"


def test_find_span_near_tier_tolerates_small_differences() -> None:
    text = (
        "1. Clinical safety\nThe clinical safety case report was last reviewed on 14 January "
        "2025 by our Clinical Safety Officer. Hazards are recorded in a hazard log."
    )
    quote = (
        "The clinical safety case report was last reveiwed on 14 Jan 2025 by our Clinical "
        "Safety Officer."
    )
    match = find_span(quote, text)
    assert match is not None
    assert match.tier == "near"
    assert 0.9 <= match.ratio < 1.0
    found = text[match.start : match.end]
    assert found.startswith("The clinical safety case report")
    assert found.endswith("Clinical Safety Officer.")
    assert match.end <= len(text)


def test_find_span_near_tier_respects_threshold() -> None:
    text = "Hazards are recorded in a hazard log that is reviewed quarterly."
    assert find_span("completely unrelated words about penguins", text) is None
    lenient = find_span("Hazards are logged and the hazard log is reviewed", text, min_ratio=0.5)
    assert lenient is not None and lenient.tier == "near"
    strict = find_span("Hazards are logged and the hazard log is reviewed", text, min_ratio=0.99)
    assert strict is None


def test_find_span_end_is_exclusive() -> None:
    text = "Alpha beta gamma."
    match = find_span("beta", text)
    assert match is not None
    assert (match.start, match.end) == (6, 10)
    assert text[match.end] == " "


@pytest.mark.parametrize("quote", ["", "   ", "­"])
def test_find_span_empty_quote_is_none(quote: str) -> None:
    assert find_span(quote, "Some text here.") is None
    assert find_span("text", "") is None


def test_normalised_equal_for_gap_matching() -> None:
    assert normalised_equal("Named deputy – CSO", "named  deputy - cso")
    assert not normalised_equal("Named deputy", "Named deputy for the CSO")


def test_find_span_near_tier_prefers_the_best_refined_window_among_near_ties() -> None:
    # Many near-identical sentences: the fixed-length windows tie before whole-word refinement.
    text = " ".join(
        f"Sentence number {i} describes the clinical safety arrangement {i} in detail."
        for i in range(120)
    )
    quote = "Sentence number 77 descrbes the clinical safty arrangement 77 in detail"
    match = find_span(quote, text)
    assert match is not None and match.tier == "near"
    # Refinement works in whole words, so the span covers the whole final word "detail.".
    assert text[match.start : match.end] == (
        "Sentence number 77 describes the clinical safety arrangement 77 in detail."
    )

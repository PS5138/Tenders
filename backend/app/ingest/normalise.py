"""The one text normaliser and span finder.

Used by the fidelity check, support verification (segments and facts), gap matching,
re-alignment and the harness. No other code matches text against sections.

``normalise(text)`` applies NFKC; maps curly quotes, en and em dashes and non-breaking spaces
to ASCII equivalents; removes soft hyphens and joins a word split by a hyphenated line break;
collapses whitespace to one space; lowercases; and returns an index mapping every normalised
position back to the original code-point offset.

``find_span(quote, text)`` returns original-text offsets (``end`` exclusive) and the tier at
which the quote was found: ``verbatim`` (exact match after normalisation) or ``near``
(windowed ``difflib`` alignment at or above ``span_match_min``), or ``None``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.config import get_settings

Tier = Literal["verbatim", "near"]

# Applied after per-character NFKC (which already folds ligatures, full-width forms, the
# ellipsis and most spaces).
_CHAR_MAP: dict[str, str] = {
    "‘": "'",  # left single quotation mark
    "’": "'",  # right single quotation mark
    "‚": "'",  # single low-9 quotation mark
    "‛": "'",  # single high-reversed-9 quotation mark
    "′": "'",  # prime
    "“": '"',  # left double quotation mark
    "”": '"',  # right double quotation mark
    "„": '"',  # double low-9 quotation mark
    "‟": '"',  # double high-reversed-9 quotation mark
    "″": '"',  # double prime
    "«": '"',  # left-pointing double angle quotation mark
    "»": '"',  # right-pointing double angle quotation mark
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    " ": " ",  # no-break space
    " ": " ",  # narrow no-break space
}
_DROP: frozenset[str] = frozenset(
    {
        "­",  # soft hyphen
        "​",  # zero width space
        "‌",  # zero width non-joiner
        "‍",  # zero width joiner
        "﻿",  # byte order mark
    }
)
_NEWLINES = frozenset({"\n", "\r", " ", " ", "\x0b", "\x0c", "\x85"})


def normalise(text: str) -> tuple[str, list[int]]:
    """Return ``(normalised, index)`` where ``index[j]`` is the original code-point offset of
    the character that produced ``normalised[j]``. Leading and trailing whitespace is dropped.
    """
    # Pass 1: NFKC per character (so the map stays exact), character mapping and removals.
    chars: list[str] = []
    origin: list[int] = []
    for position, char in enumerate(text):
        for folded in unicodedata.normalize("NFKC", char):
            mapped = _CHAR_MAP.get(folded, folded)
            if mapped in _DROP:
                continue
            chars.append(mapped)
            origin.append(position)

    # Pass 2: join a word split by a hyphenated line break ("infor-\nmation" -> "information").
    joined: list[str] = []
    joined_origin: list[int] = []
    count = len(chars)
    k = 0
    while k < count:
        char = chars[k]
        if char == "-" and k > 0 and chars[k - 1].isalpha():
            j = k + 1
            saw_newline = False
            while j < count and chars[j].isspace():
                if chars[j] in _NEWLINES:
                    saw_newline = True
                j += 1
            if saw_newline and j < count and chars[j].isalpha():
                k = j  # drop the hyphen and the line break
                continue
        joined.append(char)
        joined_origin.append(origin[k])
        k += 1

    # Pass 3: collapse whitespace runs to one space (mapped to the run's first character),
    # strip the ends, lowercase (a lowercased character may expand to several).
    out: list[str] = []
    out_origin: list[int] = []
    pending_space_at: int | None = None
    for char, position in zip(joined, joined_origin, strict=True):
        if char.isspace():
            if pending_space_at is None:
                pending_space_at = position
            continue
        if pending_space_at is not None and out:
            out.append(" ")
            out_origin.append(pending_space_at)
        pending_space_at = None
        for lowered in char.lower():
            out.append(lowered)
            out_origin.append(position)
    return "".join(out), out_origin


def to_original_span(index: list[int], start: int, end: int) -> tuple[int, int]:
    """Map a normalised half-open span back to original offsets (``end`` exclusive)."""
    if start >= end:
        raise ValueError("span must be non-empty")
    return index[start], index[end - 1] + 1


def normalised_equal(a: str, b: str) -> bool:
    """Equality after the shared normalisation; used for gap acknowledgement matching."""
    return normalise(a)[0] == normalise(b)[0]


@dataclass(frozen=True)
class SpanMatch:
    start: int  # original code-point offset, inclusive
    end: int  # original code-point offset, exclusive
    tier: Tier
    ratio: float


def _ratio(matcher: SequenceMatcher, window: str) -> float:
    matcher.set_seq1(window)
    return matcher.ratio()


def _word_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(i + 1 for i, char in enumerate(text) if char == " " and i + 1 < len(text))
    return starts


# How many of the best fixed-length windows are refined by whole words before choosing. A
# near-tie between two windows is common when the quote's length cuts a word at one of them.
_REFINE_CANDIDATES = 5


def _best_windows(
    quote: str, text: str, limit: int = _REFINE_CANDIDATES
) -> list[tuple[int, int, float]]:
    """Slide a window of the quote's length over ``text`` from each word boundary and keep
    the ``limit`` windows with the highest ``SequenceMatcher`` ratio, best first (earliest on
    ties)."""
    length = len(quote)
    matcher = SequenceMatcher(None, "", quote, autojunk=False)
    kept: list[tuple[int, int, float]] = []
    floor = -1.0  # ratio a window must beat to enter a full list
    for start in _word_starts(text):
        end = min(start + length, len(text))
        if end <= start:
            continue
        matcher.set_seq1(text[start:end])
        # Cheap upper bounds first; they only reject windows that cannot enter the list.
        if matcher.real_quick_ratio() <= floor or matcher.quick_ratio() <= floor:
            continue
        ratio = matcher.ratio()
        if ratio <= floor:
            continue
        kept.append((start, end, ratio))
        kept.sort(key=lambda item: (-item[2], item[0]))
        if len(kept) > limit:
            kept.pop()
        if len(kept) == limit:
            floor = kept[-1][2]
    return kept


def _refine_by_words(
    quote: str, text: str, start: int, end: int, ratio: float
) -> tuple[int, int, float]:
    """Trim the window's ends by whole words (or complete a partially covered word) while the
    ratio improves. Whole-word moves only, so offsets always land on word boundaries."""
    matcher = SequenceMatcher(None, "", quote, autojunk=False)
    length = len(text)
    for _ in range(64):
        candidates: list[tuple[int, int]] = []
        # Drop the first word.
        space = text.find(" ", start, end)
        if space != -1 and space + 1 < end:
            candidates.append((space + 1, end))
        # Drop the last word.
        space = text.rfind(" ", start, end)
        if space > start:
            candidates.append((start, space))
        # Include the previous word.
        if start > 0:
            previous = text.rfind(" ", 0, max(start - 1, 0))
            candidates.append((previous + 1, end))
        # Complete a partially covered last word, or include the next word.
        if end < length:
            if text[end] == " ":
                nxt = text.find(" ", end + 1)
            else:
                nxt = text.find(" ", end)
            candidates.append((start, nxt if nxt != -1 else length))
        improved = False
        for new_start, new_end in candidates:
            if new_end <= new_start:
                continue
            new_ratio = _ratio(matcher, text[new_start:new_end])
            if new_ratio > ratio + 1e-12:
                start, end, ratio = new_start, new_end, new_ratio
                improved = True
        if not improved:
            break
    return start, end, ratio


def find_span(quote: str, text: str, *, min_ratio: float | None = None) -> SpanMatch | None:
    """Locate ``quote`` in ``text``. See the module docstring for the two tiers."""
    quote_norm, _ = normalise(quote)
    text_norm, index = normalise(text)
    if not quote_norm or not text_norm:
        return None

    position = text_norm.find(quote_norm)
    if position != -1:
        start, end = to_original_span(index, position, position + len(quote_norm))
        return SpanMatch(start=start, end=end, tier="verbatim", ratio=1.0)

    threshold = get_settings().span_match_min if min_ratio is None else min_ratio
    refined = [
        _refine_by_words(quote_norm, text_norm, *window)
        for window in _best_windows(quote_norm, text_norm)
    ]
    if not refined:
        return None
    start, end, ratio = min(refined, key=lambda item: (-item[2], item[0]))
    if ratio < threshold:
        return None
    original_start, original_end = to_original_span(index, start, end)
    return SpanMatch(start=original_start, end=original_end, tier="near", ratio=ratio)

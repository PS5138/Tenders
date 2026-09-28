"""The authoritative sentence splitter and the on-stream conformer.

One deterministic splitter is used for model output, the verbatim offer and human edits. No
other code splits sentences. ``Conformer`` implements the plan's on-stream conformance rules
(draft pipeline step 8): a model segment spanning two sentences is emitted as two segments
each carrying the sources; a model segment that does not end at a sentence boundary is held
and prepended, with a single space, to the next model segment before that is split; a held
fragment is flushed on its own when the next segment's paragraph is greater, or when the
stream ends. A segment formed from several model segments carries the union of their sources
in order, the paragraph of the first and ``kind = substantive`` if any contributor is; the
other sentences of the combined text keep the sources of the one model segment they came
from. Indices are assigned in emission order and are the persisted indices.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "e.g.",
        "i.e.",
        "etc.",
        "cf.",
        "viz.",
        "vs.",
        "al.",  # et al.
        "approx.",
        "no.",
        "nos.",
        "fig.",
        "ref.",
        "para.",
        "sec.",
        "vol.",
        "pp.",
        "dr.",
        "mr.",
        "mrs.",
        "ms.",
        "prof.",
        "rev.",
        "hon.",
        "jr.",
        "sr.",
        "st.",
        "ltd.",
        "inc.",
        "co.",
        "corp.",
        "plc.",
        "dept.",
        "est.",
        "govt.",
        "u.k.",
        "u.s.",
        "n.b.",
        "p.s.",
    }
)

_CLOSERS = "\"'”’)]»"
_OPENERS = "(\"'“‘[«"
_TERMINALS = ".!?…"

_MARKER = r"(?:[-*•‣◦▪–—]|\(?(?:\d{1,3}|[a-zA-Z]|[ivxIVX]{1,4})[.)]|\d{1,3}(?:\.\d{1,3})+\.?)"
_LIST_MARKER_RE = re.compile(rf"^{_MARKER}(?=\s)")
_BARE_MARKER_RE = re.compile(rf"^{_MARKER}$")
_SHORT_MARKER_RE = re.compile(r"^\(?(?:\d{1,3}|[a-zA-Z])[.)]$")
_INITIAL_RE = re.compile(r"^[A-Za-z]\.$")
# A run of terminal punctuation, optional closers, followed by whitespace and more text.
_BOUNDARY_RE = re.compile(rf"[{re.escape(_TERMINALS)}]+[{re.escape(_CLOSERS)}]*(?=\s+\S)")
_LINE_RE = re.compile(r"[^\n]*\n?")


@dataclass(frozen=True)
class Sentence:
    text: str
    paragraph: int
    start: int  # offset into the input text
    end: int  # exclusive
    is_list_item: bool = False


def _last_token(text: str) -> str:
    parts = text.split()
    return parts[-1].lstrip(_OPENERS) if parts else ""


def _ends_with_abbreviation(token: str) -> bool:
    core = token.rstrip(_CLOSERS)
    return core.lower() in ABBREVIATIONS or bool(_INITIAL_RE.match(core))


def _sentence_ends(content: str, search_from: int) -> list[int]:
    """Exclusive end offsets of sentences inside ``content`` (the last sentence is implicit)."""
    ends: list[int] = []
    for match in _BOUNDARY_RE.finditer(content, search_from):
        end = match.end()
        token = _last_token(content[:end])
        if _ends_with_abbreviation(token):
            continue
        punctuation = match.group().rstrip(_CLOSERS)
        if "…" in punctuation or punctuation.count(".") >= 3:
            # An ellipsis ends a sentence only when what follows looks like a new one.
            following = content[end:].lstrip()
            if not following or not (
                following[0].isupper() or following[0].isdigit() or following[0] in _OPENERS
            ):
                continue
        ends.append(end)
    return ends


def split_sentences(text: str) -> list[Sentence]:
    """Split ``text`` into sentences with offsets. Paragraphs are numbered from blank lines;
    every line break is a hard boundary; a line starting with a bullet or an enumerator such
    as ``1.`` or ``a)`` is a list item whose marker stays part of its first sentence."""
    sentences: list[Sentence] = []
    paragraph = -1
    at_paragraph_start = True
    for line_match in _LINE_RE.finditer(text):
        line = line_match.group()
        if not line:
            continue
        content = line.rstrip("\r\n")
        if not content.strip():
            at_paragraph_start = True
            continue
        if at_paragraph_start:
            paragraph += 1
            at_paragraph_start = False
        lead = len(content) - len(content.lstrip())
        stripped = content.strip()
        base = line_match.start() + lead
        marker = _LIST_MARKER_RE.match(stripped)
        is_list_item = marker is not None
        ends = _sentence_ends(stripped, marker.end() if marker else 0)
        previous = 0
        for end in [*ends, len(stripped)]:
            piece = stripped[previous:end]
            if piece.strip():
                inner_lead = len(piece) - len(piece.lstrip())
                sentences.append(
                    Sentence(
                        text=piece.strip(),
                        paragraph=paragraph,
                        start=base + previous + inner_lead,
                        end=base + previous + inner_lead + len(piece.strip()),
                        is_list_item=is_list_item and previous == 0,
                    )
                )
            previous = end
    return sentences


def is_sentence_terminal(text: str) -> bool:
    """Whether the splitter's boundary rule fires at the end of ``text``: it ends with terminal
    punctuation that is not a listed abbreviation, an initial or an incomplete list item."""
    stripped = text.rstrip().rstrip(_CLOSERS)
    if not stripped:
        return False
    if _BARE_MARKER_RE.match(stripped.strip()):
        return False
    if stripped[-1] not in _TERMINALS:
        return False
    tokens = stripped.split()
    last = tokens[-1].lstrip(_OPENERS)
    if _ends_with_abbreviation(last):
        return False
    if _SHORT_MARKER_RE.match(last) and (len(tokens) == 1 or tokens[-2].endswith((":", ";"))):
        return False  # "The stages are: 1."
    return True


class ModelSegment(BaseModel):
    """Reference shape of one segment as the synthesis model emits it, before conformance.
    The synthesis owner's stream schema may subclass or mirror it."""

    text: str
    paragraph: int = 0
    kind: str = "substantive"
    sources: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class Segment:
    """A conformed segment. ``support_status`` is ``pending`` on the wire until verification."""

    index: int
    paragraph: int
    text: str
    kind: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    support_status: str = "pending"
    dispute: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "paragraph": self.paragraph,
            "text": self.text,
            "kind": self.kind,
            "sources": [dict(source) for source in self.sources],
            "dispute": self.dispute,
            "support_status": self.support_status,
        }


@dataclass
class _Held:
    text: str
    paragraph: int
    kind: str
    sources: list[dict[str, Any]]


def _coerce(segment: Any) -> tuple[str, int, str, list[dict[str, Any]]]:
    if isinstance(segment, Mapping):
        get = segment.get
    else:
        get = lambda key, default=None: getattr(segment, key, default)  # noqa: E731
    text = str(get("text", "") or "")
    paragraph = int(get("paragraph", 0) or 0)
    kind = "connective" if get("kind", "substantive") == "connective" else "substantive"
    sources = [dict(source) for source in (get("sources", None) or [])]
    return text, paragraph, kind, sources


def _source_key(source: Mapping[str, Any]) -> Any:
    source_id = source.get("source_id")
    if source_id is not None:
        return (source.get("source_type"), str(source_id))
    return tuple(sorted((str(k), repr(v)) for k, v in source.items()))


def union_sources(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Union of source lists in order, de-duplicated by (source_type, source_id)."""
    seen: set[Any] = set()
    merged: list[dict[str, Any]] = []
    for group in groups:
        for source in group:
            key = _source_key(source)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(source))
    return merged


def _merge_kind(*kinds: str) -> str:
    return "substantive" if any(kind == "substantive" for kind in kinds) else "connective"


class Conformer:
    """Stateful on-stream conformance. ``feed`` returns the segments ready to emit; ``flush``
    releases any held fragment at the end of the stream."""

    def __init__(self, *, start_index: int = 0) -> None:
        self._next_index = start_index
        self._held: _Held | None = None

    @property
    def next_index(self) -> int:
        return self._next_index

    @property
    def has_pending(self) -> bool:
        return self._held is not None

    def _emit(
        self, text: str, paragraph: int, kind: str, sources: Iterable[Mapping[str, Any]]
    ) -> Segment:
        segment = Segment(
            index=self._next_index,
            paragraph=paragraph,
            text=text,
            kind=kind,
            sources=[dict(source) for source in sources],
        )
        self._next_index += 1
        return segment

    def feed(self, model_segment: Any) -> list[Segment]:
        text, paragraph, kind, sources = _coerce(model_segment)
        emitted: list[Segment] = []
        if not text.strip():
            return emitted
        held: _Held | None = None
        if self._held is not None:
            if paragraph > self._held.paragraph:
                emitted.extend(self.flush())
            else:
                held = self._held
                self._held = None
                text = f"{held.text} {text.strip()}"
        sentences = split_sentences(text)
        if not sentences:
            return emitted
        held_len = len(held.text) if held is not None else 0

        def attribute(sentence: Sentence) -> tuple[int, str, list[dict[str, Any]]]:
            """Sources, kind and paragraph by which model segments the sentence's text came
            from: the plan's union applies only to a sentence formed from more than one model
            segment. Text that came entirely from the held fragment keeps the fragment's
            sources; text that came entirely from this segment keeps this segment's, so a
            source is never carried onto a sentence it did not support."""
            if held is None or sentence.start >= held_len:
                return paragraph, kind, sources
            if sentence.end <= held_len:
                return held.paragraph, held.kind, held.sources
            return (
                held.paragraph,
                _merge_kind(held.kind, kind),
                union_sources(held.sources, sources),
            )

        for sentence in sentences[:-1]:
            emitted.append(self._emit(sentence.text, *attribute(sentence)))
        last = sentences[-1]
        last_paragraph, last_kind, last_sources = attribute(last)
        if is_sentence_terminal(last.text):
            emitted.append(self._emit(last.text, last_paragraph, last_kind, last_sources))
        else:
            self._held = _Held(
                last.text, last_paragraph, last_kind, [dict(s) for s in last_sources]
            )
        return emitted

    def flush(self) -> list[Segment]:
        if self._held is None:
            return []
        held = self._held
        self._held = None
        return [self._emit(held.text, held.paragraph, held.kind, held.sources)]


def conform(model_segments: Iterable[Any]) -> list[Segment]:
    """Conform a complete list of model segments (non-streaming convenience)."""
    conformer = Conformer()
    result: list[Segment] = []
    for model_segment in model_segments:
        result.extend(conformer.feed(model_segment))
    result.extend(conformer.flush())
    return result


def join_segments(segments: Iterable[Mapping[str, Any] | Segment]) -> str:
    """Derive answer text from segments: sentences joined with a single space within a
    paragraph and a blank line between paragraphs."""
    paragraphs: dict[int, list[str]] = {}
    for segment in segments:
        if isinstance(segment, Segment):
            paragraph, text = segment.paragraph, segment.text
        else:
            paragraph, text = int(segment.get("paragraph", 0)), str(segment.get("text", ""))
        paragraphs.setdefault(paragraph, []).append(text)
    return "\n\n".join(" ".join(paragraphs[key]) for key in sorted(paragraphs))

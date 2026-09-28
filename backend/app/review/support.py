"""Support summary arithmetic and the one recompute function.

``summarise_support`` is the pure calculation behind ``answers.support_summary``.
``recompute_support`` is the single owner of ``support_summary`` and of clearing
``needs_review``: it runs whenever any segment's status changes or a new answer version
becomes current. It never sets ``needs_review`` true; only dispute, fact invalidation and
the ingestion step 9 removal path do that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import SegmentKind, SupportStatus
from app.db.models import Answer, Question

_ATTENTION = frozenset({SupportStatus.WEAK.value, SupportStatus.UNSUPPORTED.value})


def summarise_support(segments: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts of segments by ``support_status`` and the derived support score.

    The score is the share of substantive sentences that are ``supported`` (evidence coverage,
    not correctness), rounded to two decimals; ``None`` when there are no substantive sentences.
    """
    counts: dict[str, int] = {status.value: 0 for status in SupportStatus}
    substantive = 0
    supported = 0
    for segment in segments:
        status = str(segment.get("support_status", ""))
        counts[status] = counts.get(status, 0) + 1
        if segment.get("kind", SegmentKind.SUBSTANTIVE.value) == SegmentKind.SUBSTANTIVE.value:
            substantive += 1
            if status == SupportStatus.SUPPORTED.value:
                supported += 1
    score = round(supported / substantive, 2) if substantive else None
    return {
        "counts": counts,
        "substantive": substantive,
        "supported": supported,
        "needs_attention": substantive - supported,
        "score": score,
    }


def has_segments_needing_attention(segments: Iterable[Mapping[str, Any]]) -> bool:
    """Whether any substantive segment is ``weak`` or ``unsupported``."""
    return any(
        segment.get("kind", SegmentKind.SUBSTANTIVE.value) == SegmentKind.SUBSTANTIVE.value
        and str(segment.get("support_status", "")) in _ATTENTION
        for segment in segments
    )


def recompute_support(session: Session, answer: Answer) -> None:
    """Recompute ``answer.support_summary`` and, when ``answer`` is the question's current
    version, clear ``needs_review`` if no substantive segment is ``weak`` or ``unsupported``.
    Otherwise the flag is left exactly as it is. Flushes; the caller commits."""
    segments = list(answer.segments or [])
    answer.support_summary = summarise_support(segments)
    if answer.is_current:
        question = session.get(Question, answer.question_id)
        if question is not None and not has_segments_needing_attention(segments):
            question.needs_review = False
    session.flush()

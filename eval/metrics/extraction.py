"""Extraction metrics: pairs extracted over pairs present, and extraction fidelity.

Pairs present come from the manifest's pair list per ingested submission (the generator knows
exactly what it wrote). Pairs extracted is the count of ``qa_pair`` items on the document, and
pairs matched is how many manifest pairs the resolver finds among them, so an extraction that
produced the right number of rows from the wrong text still shows. Fidelity is the share of
extracted items with ``text_verified`` true (pairs, and all items), against
``settings.extraction_fidelity_target``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Document, KnowledgeItem, UnpairedFragment
from eval.metrics.resolution import ItemResolver


def _share(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def extraction_metrics(
    session: Session,
    manifest_documents: Sequence[Mapping[str, Any]],
    documents_by_filename: Mapping[str, Document],
    resolver: ItemResolver,
    *,
    fidelity_target: float,
) -> dict[str, Any]:
    submissions: list[dict[str, Any]] = []
    present_total = extracted_total = matched_total = 0
    for entry in manifest_documents:
        if entry.get("role") != "past_submission":
            continue
        filename = str(entry["filename"])
        document = documents_by_filename.get(filename)
        pairs = list(entry.get("pairs") or [])
        present = len(pairs) or int((entry.get("expected") or {}).get("pair_count") or 0)
        extracted = len(resolver.pairs_of(filename)) if document is not None else 0
        methods: dict[str, int] = {}
        unmatched: list[str] = []
        for pair in pairs:
            resolution = resolver.resolve(filename, str(pair.get("question_text") or ""))
            if resolution is None:
                unmatched.append(str(pair.get("number") or pair.get("concept_id")))
            else:
                methods[resolution.method] = methods.get(resolution.method, 0) + 1
        matched = present - len(unmatched)
        unverified = fragments = 0
        if document is not None:
            unverified = int(
                session.scalar(
                    select(func.count(KnowledgeItem.id)).where(
                        KnowledgeItem.document_id == document.id,
                        KnowledgeItem.text_verified.is_(False),
                    )
                )
                or 0
            )
            fragments = int(
                session.scalar(
                    select(func.count(UnpairedFragment.id)).where(
                        UnpairedFragment.document_id == document.id,
                        UnpairedFragment.resolved_item_id.is_(None),
                    )
                )
                or 0
            )
        submissions.append(
            {
                "filename": filename,
                "layout": entry.get("layout"),
                "pairs_present": present,
                "pairs_extracted": extracted,
                "pairs_matched": matched,
                "extracted_over_present": _share(extracted, present),
                "matched_over_present": _share(matched, present),
                "resolution_methods": methods,
                "unmatched_pair_numbers": unmatched,
                "unverified_items": unverified,
                "unresolved_fragments": fragments,
            }
        )
        present_total += present
        extracted_total += extracted
        matched_total += matched

    rows = session.execute(
        select(KnowledgeItem.item_type, KnowledgeItem.text_verified, func.count(KnowledgeItem.id))
        .group_by(KnowledgeItem.item_type, KnowledgeItem.text_verified)
    ).all()
    by_type: dict[str, dict[str, int]] = {}
    for item_type, verified, count in rows:
        bucket = by_type.setdefault(str(item_type), {"total": 0, "verified": 0})
        bucket["total"] += int(count)
        if verified:
            bucket["verified"] += int(count)
    items_total = sum(bucket["total"] for bucket in by_type.values())
    items_verified = sum(bucket["verified"] for bucket in by_type.values())
    pairs = by_type.get("qa_pair", {"total": 0, "verified": 0})
    fidelity_share = _share(items_verified, items_total)
    pairs_share = _share(pairs["verified"], pairs["total"])

    return {
        "submissions": submissions,
        "totals": {
            "pairs_present": present_total,
            "pairs_extracted": extracted_total,
            "pairs_matched": matched_total,
            "extracted_over_present": _share(extracted_total, present_total),
            "matched_over_present": _share(matched_total, present_total),
        },
        "fidelity": {
            "items_total": items_total,
            "items_verified": items_verified,
            "share": fidelity_share,
            "pairs_total": pairs["total"],
            "pairs_verified": pairs["verified"],
            "pairs_share": pairs_share,
            "by_item_type": by_type,
            "target": fidelity_target,
            "meets_target": fidelity_share is not None and fidelity_share >= fidelity_target,
        },
        "note": (
            "Pairs present come from the generator's manifest; the plan's hand check of one "
            "document is a separate, human step."
        ),
    }


__all__ = ["extraction_metrics"]

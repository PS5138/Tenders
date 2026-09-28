"""Support verification (step 9) against seeded sections."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.generate.verification import (
    fact_checklist_status,
    fact_is_current,
    verify_fact_checklist,
    verify_segments,
)
from tests.test_generate_fixtures import (
    FACT_STATEMENT,
    S_B,
    S_C,
    build_library,
    fact_source,
    item_source,
    script_entailment,
)


def seg(index: int, text: str, sources: list[dict], kind: str = "substantive", **extra) -> dict:  # noqa: ANN003
    return {
        "index": index,
        "paragraph": 0,
        "text": text,
        "kind": kind,
        "sources": sources,
        "dispute": None,
        "support_status": "pending",
        **extra,
    }


def test_verify_segments_locates_spans_and_assigns_statuses(
    db_session: Session,
    fake_llm,  # noqa: ANN001
) -> None:
    library = build_library(db_session)
    section = library.section
    script_entailment(fake_llm, "supported", per_sentence={"Weak one.": "weak"})
    near_quote = S_B.replace(",", "").rstrip(".")  # a comma dropped: not exact, near tier
    attestation = {
        "source_type": "human_attestation",
        "attested_by": "Jane",
        "note": None,
        "at": "2026-09-28T10:00:00Z",
    }
    segments = [
        seg(0, "Verbatim one.", [item_source(library.item, S_C.rstrip("."))]),
        seg(1, "Near one.", [item_source(library.item, near_quote)]),
        # In the section but outside the item's slice: does not count.
        seg(2, "Outside one.", [item_source(library.item, "End of section.")]),
        seg(3, "Connective.", [item_source(library.item, S_C)], kind="connective"),
        seg(4, "No sources.", []),
        seg(5, "Attested.", [attestation]),
        seg(6, "Weak one.", [item_source(library.item, S_C)]),
        seg(
            7,
            "Fact one.",
            [fact_source(library.fact, "Dr Amara Okafor, appointed on 1 March 2024")],
        ),
        seg(
            8, "Unknown item.", [item_source(library.item, S_C) | {"source_id": str(uuid.uuid4())}]
        ),
        seg(
            9,
            "Fact paraphrase.",
            [fact_source(library.fact, "the CSO is Amara Okafor since March 2024")],
            dispute={"disputed_by": "Sam", "note": "check", "at": "2026-09-28T10:00:00Z"},
        ),
    ]

    verified = verify_segments(db_session, segments)

    assert [s["index"] for s in verified] == list(range(10))
    assert [s["support_status"] for s in verified] == [
        "supported",
        "supported",
        "unsupported",
        "connective",
        "unsupported",
        "supported",
        "weak",
        "supported",
        "unsupported",
        "supported",
    ]
    assert "pending" not in {s["support_status"] for s in verified}

    verbatim = verified[0]["sources"][0]
    assert verbatim["tier"] == "verbatim"
    assert (
        section.text[verbatim["locator"]["start"] : verbatim["locator"]["end"]] == verbatim["quote"]
    )
    assert verbatim["quote"] == S_C.rstrip(".")
    assert verbatim["locator"]["start"] >= library.item.answer_start
    assert verbatim["locator"]["document_id"] == str(library.document.id)
    assert verbatim["document_title"] == library.document.filename

    near = verified[1]["sources"][0]
    assert near["tier"] == "near"
    assert section.text[near["locator"]["start"] : near["locator"]["end"]] == near["quote"]
    assert "Dr Amara Okafor" in near["quote"], "the quote is replaced with what the document says"

    outside = verified[2]["sources"][0]
    assert outside["locator"]["start"] is None and outside["locator"]["end"] is None
    assert outside["quote"] == "End of section.", (
        "an unsupported segment keeps its candidate source"
    )
    assert "tier" not in outside

    assert verified[3]["sources"][0]["locator"]["start"] is not None
    assert verified[5]["sources"] == [attestation]

    fact = verified[7]["sources"][0]
    assert fact["source_type"] == "fact"
    fact_section = library.fact_section
    assert fact_section.text[fact["locator"]["start"] : fact["locator"]["end"]] == fact["quote"]
    assert fact["quote"] == "Dr Amara Okafor, appointed on 1 March 2024"
    assert fact["locator"]["heading_path"] == ["Appendix A"]

    unknown = verified[8]["sources"][0]
    assert unknown["locator"]["start"] is None

    paraphrase = verified[9]["sources"][0]
    assert paraphrase["quote"] == FACT_STATEMENT, (
        "a paraphrased fact quote falls back to the statement"
    )
    assert verified[9]["dispute"]["disputed_by"] == "Sam", "dispute carried through"

    entailment = next(c for c in fake_llm.calls if c.name == "entailment")
    items = json.loads(entailment.user)["items"]
    assert [item["sentence"] for item in items] == [
        "Verbatim one.",
        "Near one.",
        "Weak one.",
        "Fact one.",
        "Fact paraphrase.",
    ], "one batched call over the located substantive segments only"
    assert items[0]["spans"][0]["text"] == S_C.rstrip(".")


def test_missing_entailment_verdict_is_weak_and_expired_fact_caps_at_weak(
    db_session: Session,
    fake_llm,  # noqa: ANN001
) -> None:
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    library = build_library(db_session, fact_expires_on=yesterday)
    fake_llm.register(
        "entailment", lambda **kwargs: {"verdicts": [{"id": 1, "verdict": "supported"}]}
    )
    verified = verify_segments(
        db_session,
        [
            seg(0, "No verdict.", [item_source(library.item, S_C)]),
            seg(1, "Expired fact.", [fact_source(library.fact, FACT_STATEMENT)]),
        ],
    )
    assert verified[0]["support_status"] == "weak", "no verdict returned: never supported"
    assert verified[1]["support_status"] == "weak", "the fact is expired: never supported"
    assert verified[1]["sources"][0]["locator"]["start"] is not None


def test_verify_segments_with_nothing_to_check_makes_no_llm_call(
    db_session: Session,
    fake_llm,  # noqa: ANN001
) -> None:
    verified = verify_segments(
        db_session, [seg(0, "Only connective.", [], kind="connective"), seg(1, "Bare.", [])]
    )
    assert [s["support_status"] for s in verified] == ["connective", "unsupported"]
    assert fake_llm.calls == []


def test_fact_checklist_statuses(db_session: Session) -> None:
    today = datetime.now(UTC).date()
    library = build_library(db_session)
    current = library.fact
    from app.db.models import Fact

    def make(**overrides) -> Fact:  # noqa: ANN003
        values = dict(
            org_id=current.org_id,
            document_id=current.document_id,
            section_id=current.section_id,
            knowledge_item_id=None,
            fact_kind="clinical_safety_officer",
            statement=FACT_STATEMENT,
            value="x",
            effective_date=date(2024, 3, 1),
        )
        values.update(overrides)
        fact = Fact(**values)
        db_session.add(fact)
        db_session.flush()
        return fact

    expired = make(expires_on=today - timedelta(days=1))
    superseded = make(superseded_by=current.id)
    unverified = make(statement="A statement that appears nowhere in the section.")
    entries = verify_fact_checklist(
        db_session,
        [current.id, str(expired.id), superseded.id, unverified.id, uuid.uuid4(), current.id],
    )
    assert [entry["status"] for entry in entries] == [
        "current",
        "expired",
        "superseded",
        "unverified",
    ]
    assert entries[0] == {
        "fact_id": str(current.id),
        "statement": FACT_STATEMENT,
        "effective_date": "2024-03-01",
        "status": "current",
    }
    assert fact_is_current(current, library.document)
    assert not fact_is_current(expired, library.document)
    assert not fact_is_current(superseded, library.document)
    library.document.superseded_by = library.document.id  # any pointer marks it superseded
    assert not fact_is_current(current, library.document)
    assert fact_checklist_status(current, library.document, library.fact_section) == "superseded"

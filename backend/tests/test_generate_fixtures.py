"""Builders and fakes shared by the generate tests. Not a test module in its own right: the
pytest fixtures here are imported into the test modules that use them.

The library built here is one past submission with one section holding a question and its
answer (a ``qa_pair`` item with verified offsets) and one appendix section holding a fact's
statement. Cross-module calls (retrieval, facts, the review owner's version and support
functions) are routed to fakes through the pipeline's indirection functions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID
from app.db.models import (
    Answer,
    Document,
    DocumentSection,
    Fact,
    KnowledgeItem,
    Question,
    Tender,
    Thread,
)
from app.generate import pipeline
from app.llm.embeddings import embed
from app.review.support import summarise_support

QUESTION_TEXT = (
    "Describe your clinical safety management arrangements, including how you comply with "
    "DCB0129 and name your Clinical Safety Officer."
)
S_A = (
    "Meridian Health Systems maintains a clinical safety case for the Meridian Care Record in "
    "accordance with DCB0129."
)
S_B = (
    "The safety case was last reviewed on 14 January 2025 by our Clinical Safety Officer, "
    "Dr Amara Okafor."
)
S_C = (
    "Hazards are recorded in a hazard log that is reviewed quarterly by the clinical safety group."
)
S_D = (
    "We support each deploying organisation in meeting its own DCB0160 obligations by "
    "supplying the hazard log before go-live."
)
ANSWER_TEXT = f"{S_A} {S_B} {S_C} {S_D}"
SECTION_TEXT = (
    f"3.2 Clinical safety\nQuestion: {QUESTION_TEXT}\nAnswer: {ANSWER_TEXT}\nEnd of section."
)

FACT_STATEMENT = "Our Clinical Safety Officer is Dr Amara Okafor, appointed on 1 March 2024."
FACT_SECTION_TEXT = (
    f"Appendix A: Clinical safety officer\n{FACT_STATEMENT}\n"
    "She reports to the Chief Medical Officer."
)


@dataclass
class Library:
    document: Document
    section: DocumentSection
    item: KnowledgeItem
    fact_section: DocumentSection
    fact: Fact


def build_library(
    session: Session,
    *,
    effective_date: date = date(2025, 3, 1),
    fact_expires_on: date | None = None,
) -> Library:
    document = Document(
        org_id=DEFAULT_ORG_ID,
        filename="past-submission-2025.docx",
        storage_path="storage/past-submission-2025.docx",
        doc_type="past_submission",
        effective_date=effective_date,
        effective_date_source="extracted",
        classification_confirmed=True,
        buyer="Example NHS Trust",
        submission_date=effective_date,
        ingest_status="ready",
    )
    session.add(document)
    session.flush()
    section = DocumentSection(
        org_id=DEFAULT_ORG_ID,
        document_id=document.id,
        order_index=0,
        heading_path=["3", "3.2"],
        page_start=4,
        page_end=4,
        text=SECTION_TEXT,
    )
    fact_section = DocumentSection(
        org_id=DEFAULT_ORG_ID,
        document_id=document.id,
        order_index=1,
        heading_path=["Appendix A"],
        page_start=9,
        page_end=9,
        text=FACT_SECTION_TEXT,
    )
    session.add_all([section, fact_section])
    session.flush()
    answer_start = SECTION_TEXT.index(ANSWER_TEXT)
    question_start = SECTION_TEXT.index(QUESTION_TEXT)
    item = KnowledgeItem(
        org_id=DEFAULT_ORG_ID,
        document_id=document.id,
        section_id=section.id,
        answer_start=answer_start,
        answer_end=answer_start + len(ANSWER_TEXT),
        question_section_id=None,
        question_start=question_start,
        question_end=question_start + len(QUESTION_TEXT),
        item_type="qa_pair",
        question_text=QUESTION_TEXT,
        answer_text=ANSWER_TEXT,
        text_verified=True,
        topics=["clinical_safety"],
        is_canonical=True,
        question_embedding=embed([QUESTION_TEXT])[0],
        answer_embedding=embed([ANSWER_TEXT])[0],
    )
    session.add(item)
    session.flush()
    fact = Fact(
        org_id=DEFAULT_ORG_ID,
        document_id=document.id,
        section_id=fact_section.id,
        knowledge_item_id=item.id,
        fact_kind="clinical_safety_officer",
        fact_key=None,
        statement=FACT_STATEMENT,
        value="Dr Amara Okafor",
        effective_date=date(2024, 3, 1),
        expires_on=fact_expires_on,
    )
    session.add(fact)
    session.flush()
    return Library(document, section, item, fact_section, fact)


def build_tender(session: Session, *, buyer: str = "Example NHS Trust") -> Tender:
    tender = Tender(org_id=DEFAULT_ORG_ID, name="Care record procurement", buyer=buyer)
    session.add(tender)
    session.flush()
    pack = Document(
        org_id=DEFAULT_ORG_ID,
        filename="question-pack.xlsx",
        storage_path="storage/question-pack.xlsx",
        doc_type="tender_document",
        tender_id=tender.id,
        tender_doc_kind="question_pack",
        classification_confirmed=True,
        ingest_status="ready",
    )
    session.add(pack)
    session.flush()
    tender._pack = pack  # type: ignore[attr-defined]  # test convenience
    return tender


def build_question(
    session: Session,
    *,
    tender: Tender | None = None,
    text: str = QUESTION_TEXT,
    response_type: str = "free_text",
    coverage: str = "covered",
    status: str = "not_started",
    word_limit: int | None = 300,
    number: str = "3.2",
    order_index: int = 1,
) -> Question:
    tender = tender or build_tender(session)
    pack = (
        getattr(tender, "_pack", None)
        or session.scalars(select(Document).where(Document.tender_id == tender.id)).first()
    )
    question = Question(
        org_id=DEFAULT_ORG_ID,
        tender_id=tender.id,
        document_id=pack.id,
        section="3",
        number=number,
        text=text,
        word_limit=word_limit,
        weighting=10.0,
        response_type=response_type,
        mandatory=True,
        order_index=order_index,
        topics=["clinical_safety"],
        embedding=embed([text])[0],
        coverage=coverage,
        status=status,  # fixture set-up only; production code goes through the transition function
    )
    session.add(question)
    session.flush()
    thread = Thread(
        org_id=DEFAULT_ORG_ID, tender_id=tender.id, question_id=question.id, title=f"{number}"
    )
    session.add(thread)
    session.flush()
    return question


def fake_candidate(item: KnowledgeItem, vector_score: float = 0.9) -> SimpleNamespace:
    """Owner C's ``Candidate`` shape."""
    return SimpleNamespace(
        item=item,
        fused_score=0.0325,
        vector_score=vector_score,
        lexical_rank=1,
        vector_rank=1,
        topic_rank=1,
        cluster_id=item.id,
    )


def fake_set_current_ai_version(
    session: Session, question: Question, answer: Answer, actor: str, confirm_displace: bool
) -> None:
    """Stand-in for the review owner's function: the previous current version steps down, the
    new one becomes current and status moves to ai_draft."""
    for other in session.scalars(
        select(Answer).where(Answer.question_id == question.id, Answer.is_current.is_(True))
    ):
        if other.id != answer.id:
            other.is_current = False
    session.flush()
    answer.is_current = True
    question.status = "ai_draft"
    session.flush()


def fake_recompute_support(session: Session, answer: Answer) -> None:
    answer.support_summary = summarise_support(answer.segments)


@pytest.fixture
def stubbed_modules(monkeypatch: pytest.MonkeyPatch, fake_llm, fake_embeddings):  # noqa: ANN001
    """Route the pipeline's cross-module calls to fakes. ``config.candidates`` and
    ``config.facts`` are what retrieval and fact selection return; ``config.calls`` records
    the arguments they were called with."""
    config = SimpleNamespace(candidates=[], facts=[], calls=[], fake_llm=fake_llm)

    def retrieve(session, org_id, *, query_text, query_vector, topics):  # noqa: ANN001
        config.calls.append(("retrieve", query_text, list(topics)))
        return SimpleNamespace(
            candidates=list(config.candidates),
            best_vec=max((c.vector_score for c in config.candidates), default=0.0),
        )

    def select_facts(session, org_id, candidates, topics):  # noqa: ANN001
        config.calls.append(("facts", len(candidates), list(topics)))
        return list(config.facts)

    monkeypatch.setattr(pipeline, "retrieve_candidates", retrieve)
    monkeypatch.setattr(pipeline, "select_related_facts", select_facts)
    monkeypatch.setattr(pipeline, "set_current_ai_version", fake_set_current_ai_version)
    monkeypatch.setattr(pipeline, "recompute_support", fake_recompute_support)
    return config


def script_synthesis(fake_llm, lines: list[dict[str, Any] | str], *, chunks: bool = False) -> None:  # noqa: ANN001
    """Register the synthesis stream: dicts become JSON lines, strings are emitted as given
    (for deliberately bad lines). With ``chunks`` the text is cut at awkward places so the
    line buffer is exercised."""
    text = "".join((json.dumps(line) if isinstance(line, dict) else line) + "\n" for line in lines)
    if chunks:
        pieces = [text[i : i + 7] for i in range(0, len(text), 7)]
        fake_llm.register("synthesis", pieces)
    else:
        fake_llm.register("synthesis", text)


def script_entailment(
    fake_llm,  # noqa: ANN001
    verdict: str = "supported",
    per_sentence: dict[str, str] | None = None,
) -> None:
    def respond(**kwargs: Any) -> dict[str, Any]:
        items = json.loads(kwargs["user"])["items"]
        return {
            "verdicts": [
                {"id": item["id"], "verdict": (per_sentence or {}).get(item["sentence"], verdict)}
                for item in items
            ]
        }

    fake_llm.register("entailment", respond)


def item_source(item: KnowledgeItem, quote: str) -> dict[str, str]:
    return {"source_type": "knowledge_item", "source_id": str(item.id), "quote": quote}


def fact_source(fact: Fact, quote: str) -> dict[str, str]:
    return {"source_type": "fact", "source_id": str(fact.id), "quote": quote}


CONNECTIVE = "These arrangements apply to every deployment."
UNSOURCED = "We hold a DCB0160 certificate for every Trust."
GAP = "A named deputy for the Clinical Safety Officer."


def default_synthesis_lines(library: Library) -> list[dict[str, Any] | str]:
    """The scripted model output: one segment that must be split (S_A + S_B), two that must be
    merged (S_C in two halves), a connective, two bad lines, a fact-sourced sentence and a
    sentence whose only source is unknown."""
    return [
        {
            "type": "segment",
            "text": f"{S_A} {S_B}",
            "paragraph": 0,
            "kind": "substantive",
            "sources": [
                item_source(
                    library.item,
                    "maintains a clinical safety case for the Meridian Care Record in "
                    "accordance with DCB0129",
                )
            ],
        },
        {
            "type": "segment",
            "text": "Hazards are recorded in a hazard log and",
            "paragraph": 1,
            "kind": "substantive",
            "sources": [item_source(library.item, "Hazards are recorded in a hazard log")],
        },
        {
            "type": "segment",
            "text": "reviewed quarterly by the clinical safety group.",
            "paragraph": 1,
            "kind": "substantive",
            "sources": [
                fact_source(library.fact, "Our Clinical Safety Officer is Dr Amara Okafor")
            ],
        },
        {"type": "segment", "text": CONNECTIVE, "paragraph": 1, "kind": "connective"},
        "this line is not JSON at all",
        {"type": "unknown", "text": "skipped"},
        {
            "type": "segment",
            "text": "Our Clinical Safety Officer is Dr Amara Okafor.",
            "paragraph": 2,
            "kind": "substantive",
            "sources": [fact_source(library.fact, FACT_STATEMENT)],
        },
        {
            "type": "segment",
            "text": UNSOURCED,
            "paragraph": 2,
            "kind": "substantive",
            "sources": [
                {
                    "source_type": "knowledge_item",
                    "source_id": "00000000-0000-4000-8000-00000000dead",
                    "quote": "a DCB0160 compliance certificate",
                }
            ],
        },
        {"type": "gaps", "gaps": [GAP]},
        {"type": "fact_checklist", "fact_ids": [str(library.fact.id)]},
    ]


EXPECTED_TEXTS = [
    S_A,
    S_B,
    "Hazards are recorded in a hazard log and reviewed quarterly by the clinical safety group.",
    CONNECTIVE,
    "Our Clinical Safety Officer is Dr Amara Okafor.",
    UNSOURCED,
]


class Collector:
    """Records emitted stream events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(json.loads(json.dumps(event, default=str)))

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == kind]

    @property
    def types(self) -> list[str]:
        return [event["type"] for event in self.events]

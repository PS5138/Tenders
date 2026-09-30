"""A rule-based stand-in for the language model, for the offline end-to-end test.

``HeuristicFakeLLM`` subclasses ``FakeLLM``: a response registered with ``register`` still
wins, so a test can script one call; every unregistered call is answered from the user
message by keyword rules, regular expressions over the dated fact sentences the synthetic
generator writes, and word-overlap scores. It parses exactly what each pipeline module sends:
the section labels of ``app.ingest.extract.build_window_prompt``, the chunk labels of
``app.ingest.chunk.build_annotation_prompt``, the sample of ``app.ingest.classify``, the row
labels of ``app.ingest.questions.extract_batch``, ``app.retrieve.coverage.build_judgement_input``,
the JSON items of ``app.generate.verification._entailment``, the rewrite request of
``app.generate.pipeline.rewrite_query`` and the synthesis message of
``app.generate.pipeline.build_synthesis_user_message``.

It is not a model. It knows the three synthetic layouts and the fact sentences of
``eval/synthetic/content.py``; it will extract nothing useful from a real submission. Its job
is to prove the plumbing between the modules, end to end, with no network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypeVar

from pydantic import BaseModel

from app.config import LEXICAL_STOPWORDS
from app.ingest.normalise import normalise
from app.llm.client import FakeCall, FakeLLM, FakeLLMError, History

T = TypeVar("T", bound=BaseModel)

# --- Words, dates and sentences -------------------------------------------------------------

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}
_DATE = r"(\d{1,2}) (January|February|March|April|May|June|July|August|September|October|November|December) (\d{4})"  # noqa: E501
_DATE_RE = re.compile(_DATE)
_WORD_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

# Generic English function words; sector terms never appear here.
_GENERIC_STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be by for from has have how in is it its of on or that the this to
    with will would you your we our us they their there which what when where who whom do
    does did can could should may might must not no yes any all each every both such into
    than then these those was were been being also so if but about after before between
    within without through during including include included including set out state
    give provide details detail describe explain confirm outline please whether
    """.split()
)


def parse_british_date(text: str) -> date | None:
    """``14 June 2024`` -> ``date(2024, 6, 14)``; None when no such date appears."""
    match = _DATE_RE.search(text)
    if match is None:
        return None
    return date(int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1)))


def _stem(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def content_words(text: str) -> dict[str, str]:
    """Stemmed content words of ``text`` mapped to the first surface form seen."""
    words: dict[str, str] = {}
    for token in _WORD_RE.findall(text.lower()):
        if token in _GENERIC_STOPWORDS or token in LEXICAL_STOPWORDS:
            continue
        if len(token) < 3 and not token.isdigit():
            continue
        words.setdefault(_stem(token), token)
    return words


def word_share(question: str, candidate: str) -> float:
    """Share of the question's content words that appear in the candidate text."""
    wanted = set(content_words(question))
    if not wanted:
        return 0.0
    present = set(content_words(candidate))
    return len(wanted & present) / len(wanted)


def split_sentences_simple(text: str, *, complete_only: bool = False) -> list[str]:
    """A plain sentence split for the fake's own use (the pipeline's splitter is authoritative
    and re-splits whatever the fake emits). ``complete_only`` drops lines that do not end in
    terminal punctuation, such as a chunk's heading line."""
    sentences: list[str] = []
    for line in text.split("\n"):
        for piece in _SENTENCE_RE.split(line.strip()):
            candidate = piece.strip()
            if not candidate:
                continue
            if complete_only and candidate[-1] not in ".!?":
                continue
            sentences.append(candidate)
    return sentences


def first_words(text: str, count: int = 6) -> str:
    return " ".join(text.split()[:count])


def last_words(text: str, count: int = 6) -> str:
    return " ".join(text.split()[-count:])


# --- Topics ---------------------------------------------------------------------------------

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "clinical_safety": ("dcb0129", "dcb0160", "clinical safety", "hazard", "clinical risk"),
    "information_governance": (
        "data protection",
        "toolkit",
        "information governance",
        "impact assessment",
        "controller",
        "processor",
        "retention",
        "personal data",
        "information commissioner",
    ),
    "information_security": (
        "iso/iec 27001",
        "iso 27001",
        "cyber essentials",
        "penetration",
        "vulnerabilit",
        "encrypt",
        "authentication",
        "role-based",
        "access control",
        "information security",
        "security incident",
    ),
    "interoperability": (
        "fhir",
        "hl7",
        "interoperab",
        "snomed",
        "nhs number",
        "electronic patient record",
        "integration",
        "personal demographics",
        "open standards",
    ),
    "implementation_and_onboarding": (
        "implementation",
        "go-live",
        "mobilisation",
        "project board",
        "milestone",
        "migrat",
        "cut-over",
    ),
    "training_and_support": ("training", "super-user", "e-learning", "floor-walk", "trainer"),
    "service_levels": (
        "service desk",
        "priority one",
        "availability target",
        "service level",
        "resolution target",
        "business continuity",
        "disaster recovery",
        "management information",
        "dashboards",
    ),
    "commercial_and_pricing": (
        "subscription",
        "charges",
        "contract term",
        "pricing",
        "exit",
        "invoic",
        "terms and conditions",
        "nhs standard contract",
        "successor supplier",
        "commercial",
    ),
    "social_value": (
        "carbon",
        "net zero",
        "apprenticeship",
        "social value",
        "local employment",
        "modern slavery",
        "wellbeing",
        "health inequalities",
    ),
    "company_and_experience": (
        "limited company",
        "company number",
        "headcount",
        "we employ",
        "insurance",
        "turnover",
        "subcontractor",
        "consortium",
        "financial standing",
        "legal status",
        "overview of your organisation",
        "profile of your company",
        "registration details",
        "company and experience",
    ),
    "product_functionality": (
        "wcag",
        "accessib",
        "clinicians and patients",
        "user research",
        "functionality",
    ),
}


def topics_for(text: str, taxonomy: list[str], *, limit: int = 3) -> list[str]:
    """One to three taxonomy identifiers ranked by keyword hits; the first allowed identifier
    when nothing matches (a chunk never has an empty list)."""
    lowered = text.lower()
    scored: list[tuple[int, int, str]] = []
    for position, topic in enumerate(taxonomy):
        hits = sum(lowered.count(keyword) for keyword in TOPIC_KEYWORDS.get(topic, ()))
        if hits:
            scored.append((-hits, position, topic))
    scored.sort()
    chosen = [topic for _, _, topic in scored[:limit]]
    if not chosen and taxonomy:
        chosen = [taxonomy[0]]
    return chosen


def parse_taxonomy(user: str) -> list[str]:
    match = re.search(r"^Topic taxonomy[^:\n]*:(.*)$", user, re.MULTILINE)
    if match is None:
        return []
    return re.findall(r"[a-z][a-z_]+", match.group(1))


# --- Facts ----------------------------------------------------------------------------------

FactBuilder = Callable[[re.Match[str], str], list[dict[str, Any]]]


def _iso(match: re.Match[str], sentence: str) -> list[dict[str, Any]]:
    return [
        {
            "fact_kind": "iso_27001",
            "fact_key": None,
            "statement": sentence,
            "value": match.group("num"),
            "effective_date": parse_british_date(match.group("issued")),
            "expires_on": parse_british_date(match.group("until")),
        }
    ]


def _ce_plus(match: re.Match[str], sentence: str) -> list[dict[str, Any]]:
    return [
        {
            "fact_kind": "cyber_essentials_plus",
            "fact_key": None,
            "statement": sentence,
            "value": match.group("num"),
            "effective_date": parse_british_date(match.group("issued")),
            "expires_on": parse_british_date(match.group("expires")),
        }
    ]


def _dspt(match: re.Match[str], sentence: str) -> list[dict[str, Any]]:
    return [
        {
            "fact_kind": "dspt_status",
            "fact_key": match.group("ods"),
            "statement": sentence,
            "value": f"{match.group('year')} {match.group('status').strip()}",
            "effective_date": parse_british_date(match.group("published")),
            "expires_on": None,
        }
    ]


def _named(kind: str) -> FactBuilder:
    def build(match: re.Match[str], sentence: str) -> list[dict[str, Any]]:
        return [
            {
                "fact_kind": kind,
                "fact_key": None,
                "statement": sentence,
                "value": match.group("name").strip(),
                "effective_date": None,
                "expires_on": None,
            }
        ]

    return build


def _safety_case(match: re.Match[str], sentence: str) -> list[dict[str, Any]]:
    return [
        {
            "fact_kind": "clinical_safety_case",
            "fact_key": match.group("product").strip(),
            "statement": sentence,
            "value": f"version {match.group('version')}",
            "effective_date": parse_british_date(match.group("approved")),
            "expires_on": None,
        }
    ]


def _plain(kind: str, group: str) -> FactBuilder:
    def build(match: re.Match[str], sentence: str) -> list[dict[str, Any]]:
        return [
            {
                "fact_kind": kind,
                "fact_key": None,
                "statement": sentence,
                "value": match.group(group),
                "effective_date": None,
                "expires_on": None,
            }
        ]

    return build


def _insurance(match: re.Match[str], sentence: str) -> list[dict[str, Any]]:
    covers = (
        ("public liability", "pl"),
        ("professional indemnity", "pi"),
        ("employer's liability", "el"),
        ("cyber", "cy"),
    )
    return [
        {
            "fact_kind": "insurance_cover",
            "fact_key": key,
            "statement": sentence,
            "value": match.group(group),
            "effective_date": None,
            "expires_on": None,
        }
        for key, group in covers
    ]


FACT_RULES: tuple[tuple[re.Pattern[str], FactBuilder], ...] = (
    (
        re.compile(
            r"ISO/IEC 27001 certificate, number (?P<num>[A-Z0-9-]+), was issued on "
            rf"(?P<issued>{_DATE}) by .+? and is valid until (?P<until>{_DATE})"
        ),
        _iso,
    ),
    (
        re.compile(
            r"Cyber Essentials Plus certificate, number (?P<num>[A-Z0-9-]+), was issued on "
            rf"(?P<issued>{_DATE}) by .+? and expires on (?P<expires>{_DATE})"
        ),
        _ce_plus,
    ),
    (
        re.compile(
            r"completed the (?P<year>\d{4}/\d{2}) Data Security and Protection Toolkit "
            r"assessment with an outcome of (?P<status>[A-Za-z ]+?), published on "
            rf"(?P<published>{_DATE}) under ODS code (?P<ods>[A-Z0-9]+)"
        ),
        _dspt,
    ),
    (
        re.compile(r"Our Clinical Safety Officer is (?P<name>[^,]+),"),
        _named("clinical_safety_officer"),
    ),
    (
        re.compile(r"Our Data Protection Officer is (?P<name>[^,]+), who"),
        _named("data_protection_officer"),
    ),
    (
        re.compile(
            r"clinical safety case report for (?P<product>.+?) is at version "
            r"(?P<version>[\d.]+) and was approved by our Clinical Safety Officer on "
            rf"(?P<approved>{_DATE})"
        ),
        _safety_case,
    ),
    (re.compile(r"We employ (?P<n>\d+) staff"), _plain("headcount", "n")),
    (re.compile(r"under company number (?P<n>\d+)"), _plain("company_registration", "n")),
    (
        re.compile(
            r"public liability insurance of (?P<pl>£[\d.,]+ million), professional indemnity "
            r"insurance of (?P<pi>£[\d.,]+ million), employer's liability insurance of "
            r"(?P<el>£[\d.,]+ million) and cyber insurance of (?P<cy>£[\d.,]+ million)"
        ),
        _insurance,
    ),
)

_CERTIFICATE_NUMBER_RE = re.compile(r"^Certificate number (?P<num>[A-Z0-9-]+)\.$")
_CERTIFICATE_ISSUED_RE = re.compile(rf"Certificate issued (?P<issued>{_DATE})")
_VALID_UNTIL_RE = re.compile(rf"[Vv]alid until (?P<until>{_DATE})")


def facts_in(text: str) -> list[dict[str, Any]]:
    """Dated facts stated in ``text``, each with one copied sentence as its statement."""
    facts: list[dict[str, Any]] = []
    sentences = split_sentences_simple(text)
    for sentence in sentences:
        for pattern, build in FACT_RULES:
            match = pattern.search(sentence)
            if match is not None:
                facts.extend(build(match, sentence))
        certificate = _CERTIFICATE_NUMBER_RE.match(sentence)
        if certificate is not None and "27001" in text:
            issued = _CERTIFICATE_ISSUED_RE.search(text)
            until = _VALID_UNTIL_RE.search(text)
            facts.append(
                {
                    "fact_kind": "iso_27001",
                    "fact_key": None,
                    "statement": sentence,
                    "value": certificate.group("num"),
                    "effective_date": parse_british_date(issued.group("issued"))
                    if issued
                    else None,
                    "expires_on": parse_british_date(until.group("until")) if until else None,
                }
            )
    return facts


# --- Parsed request shapes ------------------------------------------------------------------


@dataclass
class LabelledSection:
    id: str
    heading_path: list[str]
    row: str | None
    text: str


_PAIR_SECTION_RE = re.compile(
    r"^<<< section id=(?P<id>\S+) \| order (?P<order>\d+) \| heading path: (?P<path>.*?)"
    r"(?: \| row (?P<row>\S+))? >>>$",
    re.MULTILINE,
)
_CHUNK_RE = re.compile(
    r"^<<< chunk id=(?P<id>\S+) \| heading path: (?P<path>.*?) >>>$", re.MULTILINE
)
_QUESTION_SECTION_RE = re.compile(
    r"^\[section (?P<id>\S+)\] \((?P<kind>[^,)]+)(?:, row (?P<row>[^)]+))?\)\n"
    r"Heading path: (?P<path>.*)$",
    re.MULTILINE,
)
_QUESTION_LABEL_RE = re.compile(r"^Question \d+(?:\.\d+)*$")
_NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)+\s+\S")


def _split_path(path: str) -> list[str]:
    path = path.strip()
    if not path or path in ("(none)", "(no heading)"):
        return []
    return [part.strip() for part in path.split(" > ")]


def _labelled_sections(user: str, pattern: re.Pattern[str]) -> list[LabelledSection]:
    matches = list(pattern.finditer(user))
    sections: list[LabelledSection] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(user)
        text = user[match.end() : end].strip("\n")
        groups = match.groupdict()
        sections.append(
            LabelledSection(
                id=groups["id"],
                heading_path=_split_path(groups.get("path") or ""),
                row=(groups.get("row") or "").strip() or None,
                text=text,
            )
        )
    return sections


# --- Handlers -------------------------------------------------------------------------------


def classify_document(user: str, history: History | None) -> dict[str, Any]:
    buyer = re.search(r"^Buyer: (.+)$", user, re.MULTILINE)
    submitted = re.search(rf"Submitted by .+? on ({_DATE})\.", user)
    lowered = user.lower()
    if buyer is not None and submitted is not None:
        when = parse_british_date(submitted.group(1))
        return {
            "doc_type": "past_submission",
            "doc_kind": None,
            "effective_date": when,
            "buyer": buyer.group(1).strip(),
            "submission_date": when,
            "rationale": "Buyer questions answered in the supplier's voice with a submission date.",
        }
    issued = _CERTIFICATE_ISSUED_RE.search(user)
    effective = parse_british_date(issued.group("issued")) if issued else None
    if "certificate" in lowered and ("iso/iec 27001" in lowered or "iso 27001" in lowered):
        kind, why = "iso_27001", "An ISO/IEC 27001 certificate with a certification body and dates."
    elif "cyber essentials" in lowered and "certificate" in lowered:
        kind, why = "cyber_essentials_plus", "A Cyber Essentials certificate."
    elif "data security and protection toolkit" in lowered:
        kind, why = "dspt_confirmation", "A Data Security and Protection Toolkit confirmation."
    elif "clinical safety case" in lowered or "hazard log" in lowered:
        kind, why = "clinical_safety_case", "A clinical safety case or hazard log."
    elif "policy" in lowered:
        kind, why = "information_security_policy", "An internal policy document."
    else:
        kind, why = "other", "No buyer questions and no recognised certificate."
    return {
        "doc_type": "reference",
        "doc_kind": kind,
        "effective_date": effective,
        "buyer": None,
        "submission_date": None,
        "rationale": why,
    }


def _pair(
    question_section: str, answer_section: str, question: str, answer: str, taxonomy: list[str]
) -> dict[str, Any]:
    return {
        "question_section_id": question_section,
        "answer_section_id": answer_section,
        "question_start_anchor": first_words(question),
        "question_end_anchor": last_words(question),
        "answer_start_anchor": first_words(answer),
        "answer_end_anchor": last_words(answer),
        "question_text_copy": question,
        "answer_text_copy": answer,
        "topics": topics_for(f"{question} {answer}", taxonomy),
        "facts": facts_in(answer),
    }


def extract_pairs(user: str, history: History | None) -> dict[str, Any]:
    """The three synthetic layouts: adjacent cells, question heading with the answer beneath,
    and numbered questions with boxed answers that follow them in document order."""
    taxonomy = parse_taxonomy(user)
    pairs: list[dict[str, Any]] = []
    fragments: list[dict[str, Any]] = []
    pending_questions: list[tuple[str, str]] = []  # (section id, question text)
    for section in _labelled_sections(user, _PAIR_SECTION_RE):
        text = section.text.strip()
        if not text:
            continue
        if section.row is not None:
            cells = [cell.strip() for cell in text.split(" | ")]
            if len(cells) >= 2 and cells[0] and cells[-1]:
                pairs.append(_pair(section.id, section.id, cells[0], cells[-1], taxonomy))
            elif pending_questions:
                question_section, question = pending_questions.pop(0)
                pairs.append(_pair(question_section, section.id, question, text, taxonomy))
            else:
                fragments.append({"section_id": section.id, "role": "answer", "text": text})
            continue
        lines = [line.strip() for line in text.split("\n")]
        if any(_QUESTION_LABEL_RE.match(line) for line in lines):
            index = 0
            while index < len(lines):
                if _QUESTION_LABEL_RE.match(lines[index]):
                    following = index + 1
                    while following < len(lines) and not lines[following]:
                        following += 1
                    if following < len(lines):
                        pending_questions.append((section.id, lines[following]))
                        index = following + 1
                        continue
                index += 1
            continue
        if len(lines) > 1 and _NUMBERED_HEADING_RE.match(lines[0]):
            answer = "\n".join(lines[1:]).strip()
            if answer:
                pairs.append(_pair(section.id, section.id, lines[0], answer, taxonomy))
    for question_section, question in pending_questions:
        fragments.append({"section_id": question_section, "role": "question", "text": question})
    return {"pairs": pairs, "fragments": fragments}


def chunk_annotate(user: str, history: History | None) -> dict[str, Any]:
    taxonomy = parse_taxonomy(user)
    chunks = []
    for chunk in _labelled_sections(user, _CHUNK_RE):
        chunks.append(
            {
                "chunk_id": chunk.id,
                "topics": topics_for(" ".join([*chunk.heading_path, chunk.text]), taxonomy),
                "facts": facts_in(chunk.text),
            }
        )
    return {"chunks": chunks}


_RESPONSE_TYPES = {
    "free text": "free_text",
    "yes/no": "yes_no",
    "attachment": "attachment",
    "table": "table",
    "pricing": "pricing",
    "other": "other",
}
_PACK_COLUMNS = (
    "section",
    "number",
    "question",
    "word limit",
    "weighting",
    "response type",
    "mandatory",
)


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


class _Row:
    """One spreadsheet row with its cells addressed by header name, falling back to position.
    The heading path is the sheet title followed by the header cells, so the cells start at
    the first header cell."""

    def __init__(self, section: LabelledSection) -> None:
        self.cells = [cell.strip() for cell in section.text.split(" | ")]
        header = [part.lower() for part in section.heading_path]
        offset = header.index("section") if "section" in header else 0
        self.columns = {
            name: header.index(name) - offset for name in _PACK_COLUMNS if name in header
        }

    def cell(self, name: str, position: int) -> str:
        index = self.columns.get(name, position)
        return self.cells[index] if 0 <= index < len(self.cells) else ""


def extract_questions(user: str, history: History | None) -> dict[str, Any]:
    """One question per spreadsheet row: Section | Number | Question | Word limit | Weighting |
    Response type | Mandatory, mapped by the header cells in the heading path."""
    taxonomy = parse_taxonomy(user)
    questions: list[dict[str, Any]] = []
    for section in _labelled_sections(user, _QUESTION_SECTION_RE):
        if section.row is None or " | " not in section.text:
            continue
        row = _Row(section)
        text = row.cell("question", 2)
        if not text or text.lower() == "question":
            continue
        word_limit = _number(row.cell("word limit", 3))
        weighting = _number(row.cell("weighting", 4))
        response_type = _RESPONSE_TYPES.get(row.cell("response type", 5).lower(), "free_text")
        fallback = section.heading_path[0] if section.heading_path else ""
        section_label = row.cell("section", 0) or fallback
        questions.append(
            {
                "section": section_label,
                "number": row.cell("number", 1),
                "text": text,
                "word_limit": int(word_limit) if word_limit else None,
                "weighting": weighting,
                "response_type": response_type,
                "mandatory": row.cell("mandatory", 6).lower() == "yes",
                "order_index": len(questions),
                "topics": topics_for(f"{section_label} {text}", taxonomy),
            }
        )
    return {"questions": questions}


def coverage_judgement(user: str, history: History | None) -> dict[str, Any]:
    """Covered when a candidate shares at least half of the question's content words, partial
    from a fifth, otherwise new."""
    question_block = user.split("QUESTION\n", 1)[1].split("\nConstraints:", 1)[0]
    lines = [line for line in question_block.split("\n") if line.strip()]
    question = " ".join(lines[1:]) if len(lines) > 1 else (lines[0] if lines else "")
    candidates_block = user.split("\nCANDIDATES (", 1)[1] if "\nCANDIDATES (" in user else ""
    candidates = re.split(r"^\[C\d+\] ", candidates_block, flags=re.MULTILINE)[1:]
    shares = [word_share(question, candidate) for candidate in candidates]
    best = max(shares) if shares else 0.0
    wanted = content_words(question)
    covered_words: set[str] = set()
    for candidate in candidates:
        covered_words |= set(content_words(candidate))
    missing = [surface for stem, surface in wanted.items() if stem not in covered_words]
    if best >= 0.5:
        return {"coverage": "covered", "gap_summary": "", "gaps": []}
    if best >= 0.2:
        return {
            "coverage": "partial",
            "gap_summary": (
                "The library material addresses the substance of the question but does not "
                "cover " + ", ".join(missing[:5]) + "."
            ),
            "gaps": [f"Material on {word}" for word in missing[:5]],
        }
    return {
        "coverage": "new",
        "gap_summary": (
            "No candidate addresses the substance of this question; new material covering "
            + ", ".join(missing[:5])
            + " is needed."
        ),
        "gaps": [f"Material on {word}" for word in missing[:5]],
    }


_INSTRUCTION_WORDS = (
    "short",
    "bullet",
    "tone",
    "reword",
    "rewrite",
    "formal",
    "concise",
    "expand",
    "longer",
    "paragraph",
    "tighten",
    "simplif",
)


def query_rewrite(user: str, history: History | None) -> dict[str, Any]:
    """Echo the latest turn; an instruction about the draft retrieves with the tender question."""
    latest = (
        user.split("Latest user turn:\n", 1)[1].strip() if "Latest user turn:\n" in user else user
    )
    tender_question = None
    match = re.search(r"Tender question:\n(.*?)(?:\n\n|\Z)", user, re.DOTALL)
    if match is not None:
        tender_question = match.group(1).strip()
    lowered = latest.lower()
    if tender_question and any(word in lowered for word in _INSTRUCTION_WORDS):
        return {"query": tender_question}
    return {"query": latest}


def entailment(user: str, history: History | None) -> dict[str, Any]:
    """Supported when the located spans share at least two content words with the sentence."""
    payload = json.loads(user)
    verdicts = []
    for item in payload.get("items", []):
        sentence_words = set(content_words(str(item.get("sentence", ""))))
        span_words: set[str] = set()
        for span in item.get("spans", []) or []:
            span_words |= set(content_words(str(span.get("text", ""))))
        shared = len(sentence_words & span_words)
        verdicts.append({"id": int(item["id"]), "verdict": "supported" if shared >= 2 else "weak"})
    return {"verdicts": verdicts}


@dataclass
class _Candidate:
    id: str
    item_type: str
    text: str


@dataclass
class _Fact:
    id: str
    statement: str
    header: str

    @property
    def expired(self) -> bool:
        match = re.search(r"expires=(\d{4}-\d{2}-\d{2})", self.header)
        return match is not None and date.fromisoformat(match.group(1)) < date.today()


def _section_of(user: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", user, re.DOTALL | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _parse_candidates(block: str) -> list[_Candidate]:
    if not block or block.startswith("None retrieved"):
        return []
    candidates: list[_Candidate] = []
    for piece in re.split(r"\n\n(?=\[item:)", block):
        header = re.match(r"\[item:(?P<id>\S+)\] type=(?P<type>\S+)", piece)
        if header is None:
            continue
        text = piece.split("Text:\n", 1)[1].strip() if "Text:\n" in piece else ""
        candidates.append(_Candidate(header.group("id"), header.group("type"), text))
    return candidates


def _parse_facts(block: str) -> list[_Fact]:
    facts: list[_Fact] = []
    if not block or block.startswith("None"):
        return facts
    for piece in re.split(r"\n\n(?=\[fact:)", block):
        header = re.match(r"\[fact:(?P<id>\S+)\](?P<rest>.*)", piece)
        if header is None:
            continue
        statement = piece.split("Statement: ", 1)[1].strip() if "Statement: " in piece else ""
        facts.append(_Fact(header.group("id"), statement, header.group("rest")))
    return facts


@dataclass
class SynthesisRun:
    """What the fake emitted for one synthesis call (for the test's assertions)."""

    question: str
    model_segments: int
    merged_segment: bool
    fact_ids: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


def synthesis_lines(user: str, history: History | None) -> tuple[list[str], SynthesisRun]:
    """NDJSON lines per the synthesis contract: a connective opener, sentences copied verbatim
    from the top candidates (one segment deliberately holds two sentences so conformance
    splits it), each citing its item and any fact whose statement it contains, then the gaps
    and the fact checklist."""
    question = _section_of(user, "Question")
    constraints = _section_of(user, "Constraints")
    instruction = _section_of(user, "Instruction from the user")
    response_type = re.search(r"Response type: (\S+)", constraints)
    buyer = re.search(r"Buyer: (.+)", constraints)
    candidates = _parse_candidates(_section_of(user, "Candidates"))
    facts = [fact for fact in _parse_facts(_section_of(user, "Facts")) if not fact.expired]

    lines: list[str] = []
    run = SynthesisRun(question=question, model_segments=0, merged_segment=False)

    def emit(record: dict[str, Any]) -> None:
        lines.append(json.dumps(record, ensure_ascii=False))

    if response_type is not None and response_type.group(1) == "pricing":
        emit({"type": "gaps", "gaps": ["Pricing is human-only and is never drafted."]})
        emit({"type": "fact_checklist", "fact_ids": []})
        return lines, run

    normalised_facts = [(fact, normalise(fact.statement)[0]) for fact in facts if fact.statement]
    cited_facts: list[str] = []

    def sources_for(candidate: _Candidate, text: str) -> list[dict[str, Any]]:
        sources = [{"source_type": "knowledge_item", "source_id": candidate.id, "quote": text}]
        normalised_text = normalise(text)[0]
        for fact, statement in normalised_facts:
            if statement and statement in normalised_text:
                sources.append(
                    {"source_type": "fact", "source_id": fact.id, "quote": fact.statement}
                )
                if fact.id not in cited_facts:
                    cited_facts.append(fact.id)
        return sources

    def segment(text: str, paragraph: int, candidate: _Candidate | None) -> None:
        run.model_segments += 1
        record: dict[str, Any] = {
            "type": "segment",
            "text": text,
            "paragraph": paragraph,
            "kind": "connective" if candidate is None else "substantive",
            "sources": [] if candidate is None else sources_for(candidate, text),
        }
        emit(record)

    short = "short" in instruction.lower() or "concise" in instruction.lower()
    opener = "We set out below how we meet this requirement, drawing on our current submissions."
    if buyer is not None:
        opener = (
            f"We set out below how we meet this requirement for {buyer.group(1).strip()}, "
            "drawing on our current submissions."
        )
    segment(opener, 0, None)

    if candidates:
        primary = candidates[0]
        sentences = split_sentences_simple(primary.text, complete_only=True)
        limit = 4 if short else 6
        chosen = sentences[:limit]
        if len(chosen) >= 3:
            segment(chosen[0], 0, primary)
            segment(f"{chosen[1]} {chosen[2]}", 0, primary)
            run.merged_segment = True
            for sentence in chosen[3:]:
                segment(sentence, 0, primary)
        else:
            for sentence in chosen:
                segment(sentence, 0, primary)
        if not short and len(candidates) > 1:
            secondary = candidates[1]
            for sentence in split_sentences_simple(secondary.text, complete_only=True)[:2]:
                segment(sentence, 1, secondary)

    wanted = content_words(question)
    covered: set[str] = set()
    for candidate in candidates:
        covered |= set(content_words(candidate.text))
    missing = [surface for stem, surface in wanted.items() if stem not in covered]
    gaps: list[str] = []
    if missing:
        gaps.append(
            "The question also asks about "
            + ", ".join(missing[:6])
            + ", which no candidate or fact covers."
        )
    if not candidates:
        gaps.append("No library material was retrieved for this question.")
    run.gaps = gaps
    run.fact_ids = list(cited_facts)
    emit({"type": "gaps", "gaps": gaps})
    emit({"type": "fact_checklist", "fact_ids": cited_facts})
    return lines, run


ParseHandler = Callable[[str, History | None], dict[str, Any]]

PARSE_HANDLERS: dict[str, ParseHandler] = {
    "classify_document": classify_document,
    "extract_pairs": extract_pairs,
    "chunk_annotate": chunk_annotate,
    "extract_questions": extract_questions,
    "coverage_judgement": coverage_judgement,
    "query_rewrite": query_rewrite,
    "entailment": entailment,
}


class HeuristicFakeLLM(FakeLLM):
    """``LLMClient`` whose unregistered calls are answered by rules over the user message."""

    def __init__(self) -> None:
        super().__init__()
        self.synthesis_runs: list[SynthesisRun] = []

    def reset(self) -> None:
        super().reset()
        self.synthesis_runs.clear()

    def parse(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        output_model: type[T],
        max_tokens: int = 16000,
        history: History | None = None,
    ) -> T:
        if name in self._responses:
            return super().parse(
                name,
                model=model,
                system=system,
                user=user,
                output_model=output_model,
                max_tokens=max_tokens,
                history=history,
            )
        self.calls.append(FakeCall(name, model, system, user, output_model, list(history or [])))
        handler = PARSE_HANDLERS.get(name)
        if handler is None:
            raise FakeLLMError(
                f"HeuristicFakeLLM has no rule for the call '{name}'; register a response for it"
            )
        return output_model.model_validate(handler(user, history))

    def stream_text(
        self,
        name: str,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 64000,
        history: History | None = None,
    ) -> Iterator[str]:
        if name in self._responses:
            yield from super().stream_text(
                name, model=model, system=system, user=user, max_tokens=max_tokens, history=history
            )
            return
        self.calls.append(FakeCall(name, model, system, user, None, list(history or [])))
        if name != "synthesis":
            raise FakeLLMError(f"HeuristicFakeLLM has no stream rule for the call '{name}'")
        lines, run = synthesis_lines(user, history)
        self.synthesis_runs.append(run)
        for line in lines:
            yield line + "\n"


__all__ = [
    "FACT_RULES",
    "TOPIC_KEYWORDS",
    "HeuristicFakeLLM",
    "SynthesisRun",
    "chunk_annotate",
    "classify_document",
    "content_words",
    "coverage_judgement",
    "entailment",
    "extract_pairs",
    "extract_questions",
    "facts_in",
    "parse_british_date",
    "query_rewrite",
    "split_sentences_simple",
    "synthesis_lines",
    "topics_for",
    "word_share",
]

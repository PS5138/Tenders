"""Builders for the small docx and xlsx files the ingestion tests parse.

``build_submission_docx`` writes a past submission that uses all three layouts named in
ingestion step 3; ``build_reference_docx`` a short certificate-like reference document;
``build_pack_xlsx`` a tabular question pack. Kept in a test module so the other
``test_ingest_*`` files can import them; the one test here checks the files open.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import DEFAULT_ORG_ID
from app.db.models import Document, DocumentSection
from app.ingest.parse import parse_document, persist_sections

# --- Text used by the tests (so anchors can be derived from the same strings) -------------------

PREAMBLE = "Response to Barts Health NHS Trust. Submitted 14 March 2025 by Example Health Ltd."

Q11 = "1.1 Describe how you ensure the clinical safety of your product."
A11_LINES = [
    "Our product is developed and maintained under DCB0129 with a named Clinical Safety Officer.",
    "Our Clinical Safety Officer, Dr Amira Patel, is a registered clinician who signs off every "
    "release.",
    "The clinical safety case report for Scribe was released on 2 February 2025 and covers all "
    "identified hazards.",
]
Q12 = "1.2 Describe your hazard log process."
A12_LINES = [
    "Hazards are recorded in a controlled hazard log reviewed at every release.",
    "Each hazard carries an initial and residual risk score agreed with the deploying trust.",
]

TABLE_HEADER = ("Question", "Response")
Q21 = "2.1 Confirm your Data Security and Protection Toolkit status."
A21 = (
    "We published Standards Met for 2024-25 under ODS code X26 on 28 June 2025. "
    "Our next assessment is scheduled for June 2026."
)
Q22 = "2.2 Confirm your Cyber Essentials Plus certification."
A22 = (
    "We hold Cyber Essentials Plus certificate IASME-CEP-004211, certified on 12 January 2025 "
    "and expiring on 11 January 2026. The certificate covers all corporate devices."
)

Q31 = "3.1 Outline your implementation approach for a large acute trust."
A31 = (
    "Implementation runs in three phases over twelve weeks: discovery, configuration and "
    "go-live. A named implementation lead works with the trust's project team throughout, "
    "and go-live is supported on site for the first two weeks."
)
Q32 = "3.2 Outline your training offer."
A32 = (
    "Training is delivered as ninety-minute role-based sessions, with e-learning available "
    "from day one and floor-walking support during go-live."
)
Q33 = "3.3 Provide your mobilisation plan as an attachment."
A33_PLACEHOLDER = "See attached."

REFERENCE_HEADING = "Certificate of Registration"
REFERENCE_LINES = [
    "This is to certify that Example Health Ltd operates an Information Security Management "
    "System which complies with the requirements of ISO/IEC 27001:2022.",
    "Certificate number IS 771234. Date of initial certification 1 March 2025. "
    "Certificate expiry 28 February 2028.",
    "Scope: the provision of clinical documentation software and associated support services.",
]

PACK_HEADER = ("Ref", "Section", "Question", "Word limit")
PACK_ROWS = [
    ("CS1", "Clinical safety", "Describe how you ensure the clinical safety of your product.", 500),
    ("IG1", "Information governance", "Confirm your DSPT status for the current year.", 200),
    ("IM1", "Implementation", "Outline your implementation approach.", 750),
]


# --- Builders -----------------------------------------------------------------------------------


def build_submission_docx(path: Path) -> Path:
    from docx import Document as DocxDocument

    doc = DocxDocument()
    doc.add_paragraph(PREAMBLE)

    # Layout 2: question as heading with the answer beneath.
    doc.add_heading("1. Clinical safety", level=1)
    doc.add_heading(Q11, level=2)
    for line in A11_LINES:
        doc.add_paragraph(line)
    doc.add_heading(Q12, level=2)
    for line in A12_LINES:
        doc.add_paragraph(line)

    # Layout 1: question and answer in adjacent table cells.
    doc.add_heading("2. Information security", level=1)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = TABLE_HEADER
    for question, answer in ((Q21, A21), (Q22, A22)):
        row = table.add_row()
        row.cells[0].text = question
        row.cells[1].text = answer

    # Layout 3: numbered form with a boxed answer cell.
    doc.add_heading("3. Implementation", level=1)
    for question, answer in ((Q31, A31), (Q32, A32), (Q33, A33_PLACEHOLDER)):
        doc.add_paragraph(question)
        box = doc.add_table(rows=1, cols=1)
        box.rows[0].cells[0].text = answer

    doc.save(str(path))
    return path


def build_reference_docx(path: Path, lines: Sequence[str] | None = None) -> Path:
    from docx import Document as DocxDocument

    doc = DocxDocument()
    doc.add_heading(REFERENCE_HEADING, level=1)
    for line in lines or REFERENCE_LINES:
        doc.add_paragraph(line)
    doc.save(str(path))
    return path


def build_pack_xlsx(path: Path) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Questions"
    sheet.append(list(PACK_HEADER))
    for row in PACK_ROWS:
        sheet.append(list(row))
    notes = workbook.create_sheet("Notes")
    notes.append(["Guidance"])
    notes.append(["Answers must not exceed the stated word limit."])
    workbook.save(str(path))
    return path


# --- Database helpers ---------------------------------------------------------------------------


def make_document(
    session: Session,
    storage_path: Path | str,
    *,
    filename: str | None = None,
    doc_type: str | None = None,
    ingest_status: str = "queued",
    **fields: object,
) -> Document:
    document = Document(
        org_id=DEFAULT_ORG_ID,
        filename=filename or Path(str(storage_path)).name,
        storage_path=str(storage_path),
        doc_type=doc_type,
        ingest_status=ingest_status,
        **fields,
    )
    session.add(document)
    session.flush()
    return document


def parse_and_persist(session: Session, document: Document) -> list[DocumentSection]:
    parsed = parse_document(Path(document.storage_path), document.filename)
    return persist_sections(session, document, parsed)


def section_starting(sections: Sequence[DocumentSection], prefix: str) -> DocumentSection:
    for section in sections:
        if section.text.startswith(prefix):
            return section
    raise AssertionError(f"no section starts with {prefix!r}")


def section_containing(sections: Sequence[DocumentSection], needle: str) -> DocumentSection:
    for section in sections:
        if needle in section.text:
            return section
    raise AssertionError(f"no section contains {needle!r}")


def anchors(text: str, words: int = 5) -> tuple[str, str]:
    """First and last ``words`` words of ``text``, as the extraction prompt asks for."""
    tokens = text.split()
    return " ".join(tokens[:words]), " ".join(tokens[-words:])


def new_id() -> str:
    return str(uuid.uuid4())


# --- Sanity ------------------------------------------------------------------------------------


def test_builders_produce_openable_files(tmp_path: Path) -> None:
    from docx import Document as DocxDocument
    from openpyxl import load_workbook

    submission = build_submission_docx(tmp_path / "submission.docx")
    reference = build_reference_docx(tmp_path / "reference.docx")
    pack = build_pack_xlsx(tmp_path / "pack.xlsx")
    assert len(DocxDocument(str(submission)).tables) == 4
    assert len(DocxDocument(str(reference)).paragraphs) >= 4
    assert load_workbook(str(pack)).sheetnames == ["Questions", "Notes"]

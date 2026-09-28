"""GET /sections/{id}: the source pane's read of a stored section with an optional highlight.

Implemented in the skeleton because the fixture answer's locators resolve against it. It
reads document_sections and offsets only; it never opens the file or searches for text.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession, OrgId
from app.api.schemas import Highlight, SectionDocument, SectionRecord, SectionResponse
from app.db.models import Document, DocumentSection

router = APIRouter(tags=["sections"])


@router.get("/sections/{section_id}", response_model=SectionResponse)
def get_section(
    section_id: uuid.UUID,
    db: DbSession,
    org_id: OrgId,
    start: int | None = Query(default=None, ge=0),
    end: int | None = Query(default=None, ge=0),
) -> SectionResponse:
    section = db.get(DocumentSection, section_id)
    if section is None or section.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Section not found.")
    document = db.get(Document, section.document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Section not found.")

    highlight: Highlight | None = None
    if start is not None and end is not None and start < end <= len(section.text):
        highlight = Highlight(start=start, end=end)

    return SectionResponse(
        section=SectionRecord.model_validate(section),
        document=SectionDocument.model_validate(document),
        highlight=highlight,
    )

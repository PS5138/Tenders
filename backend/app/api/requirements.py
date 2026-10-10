"""Specification requirements: the tender's list with its compliance summary, the human rating
and the manual re-scan (plan: API surface, Requirements).

Compliance class (A compliant now, B compliant by a date, C cannot comply) is set by a person
on each requirement; the interface shows it as Green, Amber and Red. The AI suggestion is
returned beside it and never counts until accepted, which is a PATCH like any other rating.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import Actor, DbSession, OrgId
from app.api.schemas import (
    RequirementList,
    RequirementLocator,
    RequirementRecord,
    RequirementSummary,
    RescanResponse,
)
from app.db import enums as e
from app.db.models import Document, DocumentSection, Requirement, Tender, utcnow
from app.ingest.requirements import request_requirements_scan
from app.review.events import record_event

router = APIRouter(tags=["requirements"])

class RequirementPatch(BaseModel):
    """Any of the human fields. ``compliance_class: null`` clears the rating."""

    compliance_class: Literal["A", "B", "C"] | None = None
    compliant_by: date | None = None
    comment: str | None = Field(default=None, max_length=4000)
    owner: str | None = Field(default=None, max_length=200)

    @field_validator("comment", "owner")
    @classmethod
    def _blank_is_null(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


# --- Helpers ----------------------------------------------------------------------------------


def requirement_summaries(
    db: Session, tender_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RequirementSummary]:
    """Counts by confirmed class per tender, in one grouped query: ``green`` is A, ``amber`` B
    and ``red`` C, the interface's names (colours are display only and never stored)."""
    result = {tender_id: RequirementSummary() for tender_id in tender_ids}
    if not tender_ids:
        return result
    unrated = Requirement.compliance_class.is_(None)

    def count_class(value: str):  # noqa: ANN202
        return func.coalesce(func.sum(case((Requirement.compliance_class == value, 1), else_=0)), 0)

    stmt = (
        select(
            Requirement.tender_id,
            func.count(Requirement.id),
            count_class(e.ComplianceClass.A.value),
            count_class(e.ComplianceClass.B.value),
            count_class(e.ComplianceClass.C.value),
            func.coalesce(func.sum(case((unrated, 1), else_=0)), 0),
            func.coalesce(
                func.sum(
                    case((unrated & Requirement.suggested_class.isnot(None), 1), else_=0)
                ),
                0,
            ),
        )
        .where(Requirement.tender_id.in_(tender_ids))
        .group_by(Requirement.tender_id)
    )
    for row in db.execute(stmt):
        result[row[0]] = RequirementSummary(
            total=int(row[1]),
            green=int(row[2]),
            amber=int(row[3]),
            red=int(row[4]),
            unrated=int(row[5]),
            suggested_unrated=int(row[6]),
        )
    return result


def _record(
    requirement: Requirement, document: Document, section: DocumentSection
) -> RequirementRecord:
    return RequirementRecord(
        id=requirement.id,
        tender_id=requirement.tender_id,
        document_id=requirement.document_id,
        document_filename=document.filename,
        tender_doc_kind=document.tender_doc_kind,
        locator=RequirementLocator(
            document_id=section.document_id,
            section_id=section.id,
            start=requirement.start,
            end=requirement.end,
            page=section.page_start,
            table=section.table_index,
            cell_ref=section.cell_ref,
            heading_path=list(section.heading_path or []),
        ),
        ref=requirement.ref,
        text=requirement.text,
        priority=requirement.priority,
        order_index=requirement.order_index,
        topics=list(requirement.topics or []),
        suggested_class=requirement.suggested_class,
        suggestion=requirement.suggestion or {},
        compliance_class=requirement.compliance_class,
        compliant_by=requirement.compliant_by,
        comment=requirement.comment,
        owner=requirement.owner,
        rated_by=requirement.rated_by,
        rated_at=requirement.rated_at,
        created_at=requirement.created_at,
        updated_at=requirement.updated_at,
    )


def _get_tender(db: Session, tender_id: uuid.UUID, org_id: uuid.UUID) -> Tender:
    tender = db.get(Tender, tender_id)
    if tender is None or tender.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tender not found.")
    return tender


def _load_record(db: Session, requirement: Requirement) -> RequirementRecord:
    document = db.get(Document, requirement.document_id)
    section = db.get(DocumentSection, requirement.section_id)
    assert document is not None and section is not None  # both cascade with the row
    return _record(requirement, document, section)


# --- Endpoints --------------------------------------------------------------------------------


@router.get("/tenders/{tender_id}/requirements", response_model=RequirementList)
def list_requirements(tender_id: uuid.UUID, db: DbSession, org_id: OrgId) -> RequirementList:
    """{summary: {green, amber, red, unrated, total, suggested_unrated}, job: the latest
    extract_requirements job id or null, requirements: [...]} in document order."""
    tender = _get_tender(db, tender_id, org_id)
    rows = db.execute(
        select(Requirement, Document, DocumentSection)
        .join(Document, Document.id == Requirement.document_id)
        .join(DocumentSection, DocumentSection.id == Requirement.section_id)
        .where(Requirement.tender_id == tender.id, Requirement.org_id == org_id)
        .order_by(Requirement.order_index, Requirement.created_at, Requirement.id)
    ).all()
    return RequirementList(
        summary=requirement_summaries(db, [tender.id])[tender.id],
        job=tender.requirements_job_id,
        requirements=[_record(row, document, section) for row, document, section in rows],
    )


@router.post(
    "/tenders/{tender_id}/requirements/rescan",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RescanResponse,
)
def rescan_requirements(
    tender_id: uuid.UUID, db: DbSession, org_id: OrgId, actor: Actor
) -> RescanResponse:
    """Enqueue an extract_requirements run over every ready tender document, or return the one
    already queued. Ratings, comments and owners are kept by the re-run."""
    tender = _get_tender(db, tender_id, org_id)
    job = request_requirements_scan(db, tender, actor)
    db.commit()
    return RescanResponse(job_id=job.id)


@router.patch("/requirements/{requirement_id}", response_model=RequirementRecord)
def patch_requirement(
    requirement_id: uuid.UUID,
    body: RequirementPatch,
    db: DbSession,
    org_id: OrgId,
    actor: Actor,
) -> RequirementRecord:
    """Set any of compliance_class, compliant_by, comment and owner. A change of
    compliance_class stamps rated_by and rated_at from X-Actor and writes a
    ``requirement_rated`` event with from and to; clearing it clears both stamps. Moving to A
    or C clears compliant_by unless the same request sets it."""
    requirement = db.get(Requirement, requirement_id)
    if requirement is None or requirement.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Requirement not found.")
    changes = body.model_dump(exclude_unset=True)
    previous = requirement.compliance_class
    for name in ("comment", "owner", "compliant_by"):
        if name in changes:
            setattr(requirement, name, changes[name])
    if "compliance_class" in changes and changes["compliance_class"] != previous:
        new_class = changes["compliance_class"]
        requirement.compliance_class = new_class
        if new_class is None:
            requirement.rated_by = None
            requirement.rated_at = None
        else:
            requirement.rated_by = actor
            requirement.rated_at = utcnow()
        if new_class in (e.ComplianceClass.A.value, e.ComplianceClass.C.value) and (
            "compliant_by" not in changes
        ):
            requirement.compliant_by = None
        db.flush()
        record_event(
            db,
            e.EntityType.REQUIREMENT,
            requirement.id,
            e.EventType.REQUIREMENT_RATED,
            actor,
            {
                "from": previous,
                "to": new_class,
                "tender_id": str(requirement.tender_id),
                "ref": requirement.ref,
                "compliant_by": (
                    requirement.compliant_by.isoformat() if requirement.compliant_by else None
                ),
                "accepted_suggestion": new_class is not None
                and new_class == requirement.suggested_class,
            },
            org_id=org_id,
        )
    db.commit()
    db.refresh(requirement)
    return _load_record(db, requirement)


__all__ = ["RequirementPatch", "requirement_summaries", "router"]

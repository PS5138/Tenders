"""Service-only provisioning. User authentication stays in the Next.js application."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import Actor, DbSession, OrgId
from app.config import DEFAULT_TOPIC_TAXONOMY
from app.db.models import Organisation

router = APIRouter(tags=["organisations"])


class OrganisationCreate(BaseModel):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    # A demonstration business: synthetic providers and synthetic uploads only, whatever the
    # deployment's providers are (``app.llm.scope``). Omitted means False on creation and
    # "leave as it is" on a repeat call, so re-provisioning never flips it by accident.
    synthetic: bool | None = None


class OrganisationRecord(BaseModel):
    id: uuid.UUID
    name: str
    synthetic: bool


def _record(organisation: Organisation) -> OrganisationRecord:
    return OrganisationRecord(
        id=organisation.id, name=organisation.name, synthetic=organisation.synthetic
    )


@router.post("/organisations", response_model=OrganisationRecord)
def provision(body: OrganisationCreate, db: DbSession, actor: Actor) -> OrganisationRecord:
    """Caller-generated IDs make retries safe across the two databases."""
    organisation = db.get(Organisation, body.id)
    if organisation is not None and organisation.name != body.name:
        raise HTTPException(409, "This organisation identifier already has a different name.")
    if organisation is None:
        organisation = Organisation(
            id=body.id,
            org_id=body.id,
            name=body.name,
            topic_taxonomy=list(DEFAULT_TOPIC_TAXONOMY),
            synthetic=bool(body.synthetic),
        )
        db.add(organisation)
        db.commit()
    elif body.synthetic is not None and organisation.synthetic != body.synthetic:
        organisation.synthetic = body.synthetic
        db.commit()
    return _record(organisation)


@router.get("/organisations/current", response_model=OrganisationRecord)
def current(db: DbSession, org_id: OrgId) -> OrganisationRecord:
    """The organisation X-Org-Id names, so the front end can label a synthetic business."""
    organisation = db.get(Organisation, org_id)
    if organisation is None:
        raise HTTPException(404, "Organisation not found.")
    return _record(organisation)

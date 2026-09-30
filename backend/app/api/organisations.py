"""Service-only provisioning. User authentication stays in the Next.js application."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import Actor, DbSession
from app.config import DEFAULT_TOPIC_TAXONOMY
from app.db.models import Organisation

router = APIRouter(tags=["organisations"])


class OrganisationCreate(BaseModel):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


class OrganisationRecord(BaseModel):
    id: uuid.UUID
    name: str


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
        )
        db.add(organisation)
        db.commit()
    return OrganisationRecord(id=organisation.id, name=organisation.name)

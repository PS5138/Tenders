"""GET /jobs/{id}: poll a background job."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession, OrgId
from app.api.schemas import JobRecord
from app.db.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRecord)
def get_job(job_id: uuid.UUID, db: DbSession, org_id: OrgId) -> JobRecord:
    """{id, kind, status, attempts, done, total, error, started_at, finished_at, next_job_id,
    results}. The payload is backend-internal and never returned."""
    job = db.get(Job, job_id)
    if job is None or job.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return JobRecord.model_validate(job)

"""Seed the default organisation and the pre-parsed fixture document.

Idempotent: re-running updates the organisation name and taxonomy if missing, and inserts
any fixture section that is absent. No parsing happens here and no knowledge items are
created, so the fixture never enters retrieval. Run as ``python -m app.db.seed``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import (
    DEFAULT_ORG_ID,
    DEFAULT_TOPIC_TAXONOMY,
    FACT_KINDS,
    FIXTURE_DOCUMENT_ID,
    get_settings,
)
from app.db import enums as e
from app.db.models import Document, DocumentSection, Organisation
from app.db.session import new_session

logger = logging.getLogger(__name__)

DEFAULT_ORG_NAME = "Default organisation"


class SeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeedResult:
    organisation_id: uuid.UUID
    document_id: uuid.UUID
    sections_inserted: int
    sections_total: int


def load_fixture_document(path: Path | None = None) -> dict[str, Any]:
    fixture_path = Path(path or get_settings().fixture_document_path)
    if not fixture_path.exists():
        raise SeedError(f"fixture document not found at {fixture_path}")
    with fixture_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if "document" not in data or "sections" not in data:
        raise SeedError("fixture document JSON must contain 'document' and 'sections'")
    return data


def _assert_taxonomy_covers_fact_kinds(taxonomy: list[str]) -> None:
    required = {kind.topic for kind in FACT_KINDS.values()}
    missing = sorted(required - set(taxonomy))
    if missing:
        raise SeedError(
            "topic taxonomy is missing topics referenced by the fact-kind mapping: "
            + ", ".join(missing)
        )


def seed_organisation(session: Session) -> Organisation:
    organisation = session.get(Organisation, DEFAULT_ORG_ID)
    if organisation is None:
        organisation = Organisation(
            id=DEFAULT_ORG_ID,
            org_id=DEFAULT_ORG_ID,
            name=DEFAULT_ORG_NAME,
            topic_taxonomy=list(DEFAULT_TOPIC_TAXONOMY),
        )
        session.add(organisation)
    elif not organisation.topic_taxonomy:
        organisation.topic_taxonomy = list(DEFAULT_TOPIC_TAXONOMY)
    _assert_taxonomy_covers_fact_kinds(list(organisation.topic_taxonomy))
    session.flush()
    return organisation


def seed_fixture_document(
    session: Session, organisation: Organisation, *, fixture_path: Path | None = None
) -> tuple[Document, int, int]:
    data = load_fixture_document(fixture_path)
    doc_data = data["document"]
    document_id = uuid.UUID(doc_data["id"])
    if document_id != FIXTURE_DOCUMENT_ID:
        raise SeedError(
            f"fixture document id {document_id} does not match FIXTURE_DOCUMENT_ID "
            f"{FIXTURE_DOCUMENT_ID}"
        )

    document = session.get(Document, document_id)
    if document is None:
        document = Document(
            id=document_id,
            org_id=organisation.id,
            filename=doc_data["filename"],
            storage_path=doc_data.get("storage_path", "fixtures/fixture_document.json"),
            doc_type=e.DocType.REFERENCE.value,
            doc_kind="other",
            effective_date=date.fromisoformat(doc_data["effective_date"]),
            effective_date_source=e.EffectiveDateSource.USER.value,
            classification_confirmed=True,
            ingest_status=e.IngestStatus.READY.value,
        )
        session.add(document)
        session.flush()

    inserted = 0
    sections = data["sections"]
    for section_data in sections:
        section_id = uuid.UUID(section_data["id"])
        if session.get(DocumentSection, section_id) is not None:
            continue
        session.add(
            DocumentSection(
                id=section_id,
                org_id=organisation.id,
                document_id=document.id,
                order_index=int(section_data["order_index"]),
                heading_path=list(section_data.get("heading_path") or []),
                page_start=section_data.get("page_start"),
                page_end=section_data.get("page_end"),
                table_index=section_data.get("table_index"),
                cell_ref=section_data.get("cell_ref"),
                text=section_data["text"],
            )
        )
        inserted += 1
    session.flush()
    return document, inserted, len(sections)


def seed(session: Session, *, fixture_path: Path | None = None) -> SeedResult:
    """Seed everything. Does not commit; the caller owns the transaction."""
    organisation = seed_organisation(session)
    document, inserted, total = seed_fixture_document(
        session, organisation, fixture_path=fixture_path
    )
    return SeedResult(
        organisation_id=organisation.id,
        document_id=document.id,
        sections_inserted=inserted,
        sections_total=total,
    )


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    session = new_session()
    try:
        result = seed(session)
        session.commit()
    finally:
        session.close()
    logger.info(
        "seeded organisation %s and fixture document %s (%d of %d sections inserted)",
        result.organisation_id,
        result.document_id,
        result.sections_inserted,
        result.sections_total,
    )


if __name__ == "__main__":
    main()

"""Migrations apply from an empty database to head and back, and the head schema carries the
event types added in 0002 (tender_submitted, outcome_set, supersession_reversed)."""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import enums as e
from app.db.models import Event
from app.review.events import record_event

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = "0002"
NEW_EVENT_TYPES = ("tender_submitted", "outcome_set", "supersession_reversed")


def _event_type_constraint(session: Session) -> str:
    return session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_events_event_type'"
        )
    ).scalar_one()


def test_head_revision_and_event_type_constraint(db_session: Session) -> None:
    version = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == HEAD_REVISION
    definition = _event_type_constraint(db_session)
    for event_type in e.values(e.EventType):
        assert f"'{event_type}'" in definition
    for event_type in NEW_EVENT_TYPES:
        assert event_type in e.values(e.EventType)


def test_new_event_types_are_writable(db_session: Session) -> None:
    entity_id = uuid.uuid4()
    for event_type in NEW_EVENT_TYPES:
        record_event(db_session, e.EntityType.TENDER, entity_id, event_type, "test user", {})
    written = db_session.query(Event).filter(Event.entity_id == entity_id).all()
    assert sorted(event.event_type for event in written) == sorted(NEW_EVENT_TYPES)


def test_upgrade_from_base_to_head_and_back(database_url: str) -> None:
    """A fresh database on the same pgserver: base -> head applies 0001 and 0002; head -> base
    removes everything again. The suite's own database is untouched."""
    db_name = f"tenders_migrate_{uuid.uuid4().hex[:8]}"
    admin_url = database_url.replace("+psycopg", "", 1)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{db_name}"')
    fresh_url = database_url.rsplit("/", 1)[0] + f"/{db_name}"
    if "?" in database_url:
        base, query = database_url.split("?", 1)
        fresh_url = base.rsplit("/", 1)[0] + f"/{db_name}?{query}"
    engine = create_engine(fresh_url, poolclass=None)
    try:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.attributes["configure_logger"] = False
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        with Session(engine) as session:
            version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == HEAD_REVISION
            definition = _event_type_constraint(session)
            for event_type in NEW_EVENT_TYPES:
                assert f"'{event_type}'" in definition
            tables = set(
                session.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            )
            assert {"events", "jobs", "tenders", "questions", "answers", "documents"} <= tables

        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, "0001")
        with Session(engine) as session:
            definition = _event_type_constraint(session)
            for event_type in NEW_EVENT_TYPES:
                assert f"'{event_type}'" not in definition
            assert "'answer_promoted'" in definition

        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, "base")
        with Session(engine) as session:
            tables = set(
                session.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            )
            assert "events" not in tables
    finally:
        engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')

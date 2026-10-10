"""Per-organisation providers: a synthetic business keeps the stand-ins beside live ones."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.enums import JobKind
from app.db.models import Organisation
from app.jobs import enqueue
from app.llm.client import AnthropicLLM, reset_llm
from app.llm.embeddings import FakeEmbedder, OpenAIEmbedder, get_embedder, reset_embedder
from app.llm.scope import is_synthetic, org_scope, run_in_context
from app.llm.synthetic import HeuristicFakeLLM


def make_org(session: Session, *, synthetic: bool) -> Organisation:
    org_id = uuid.uuid4()
    organisation = Organisation(
        id=org_id, org_id=org_id, name=f"Org {org_id}", topic_taxonomy=[], synthetic=synthetic
    )
    session.add(organisation)
    session.flush()
    return organisation


@pytest.fixture
def live_providers(settings_override: Callable[..., Settings]) -> Iterator[None]:
    """Anthropic and OpenAI selected with placeholder keys; no client makes a call."""
    settings_override(
        llm_provider="anthropic",
        embedding_provider="openai",
        anthropic_api_key="sk-test",
        openai_api_key="sk-test",
        synthetic_demo="false",
    )
    reset_llm()
    reset_embedder()
    yield
    reset_llm()
    reset_embedder()


def test_providers_follow_the_organisation_scope(live_providers: None) -> None:
    from app.llm.client import get_llm

    assert isinstance(get_llm(), AnthropicLLM)
    assert isinstance(get_embedder(), OpenAIEmbedder)
    with org_scope(True):
        assert is_synthetic()
        assert isinstance(get_llm(), HeuristicFakeLLM)
        assert isinstance(get_embedder(), FakeEmbedder)
    assert not is_synthetic()
    assert isinstance(get_llm(), AnthropicLLM), "the scope ends with the block"


def test_scope_reaches_pool_threads_only_through_run_in_context() -> None:
    with org_scope(True), ThreadPoolExecutor(max_workers=4) as pool:
        bare = list(pool.map(lambda _: is_synthetic(), range(8)))
        carried = list(pool.map(run_in_context(lambda _: is_synthetic()), range(8)))
    assert bare == [False] * 8, "pool threads do not inherit context variables"
    assert carried == [True] * 8


async def test_provisioning_sets_and_keeps_the_flag(app_client: httpx.AsyncClient) -> None:
    body = {"id": str(uuid.uuid4()), "name": "Demonstration business", "synthetic": True}
    created = await app_client.post("/organisations", json=body)
    assert created.status_code == 200 and created.json()["synthetic"] is True
    # A repeat call without the field leaves the flag as it is.
    repeat = await app_client.post("/organisations", json={"id": body["id"], "name": body["name"]})
    assert repeat.json()["synthetic"] is True
    current = await app_client.get("/organisations/current", headers={"X-Org-Id": body["id"]})
    assert current.json() == body
    turned_off = await app_client.post("/organisations", json={**body, "synthetic": False})
    assert turned_off.json()["synthetic"] is False


async def test_uploads_are_gated_per_organisation(
    app_client: httpx.AsyncClient, db_session: Session
) -> None:
    synthetic = make_org(db_session, synthetic=True)
    live = make_org(db_session, synthetic=False)
    file = {"file": ("own-document.txt", b"A supplier's own document.")}

    refused = await app_client.post(
        "/documents", files=file, headers={"X-Org-Id": str(synthetic.id)}
    )
    assert refused.status_code == 422, "a synthetic business takes only the supplied files"
    accepted = await app_client.post("/documents", files=file, headers={"X-Org-Id": str(live.id)})
    assert accepted.status_code == 202, accepted.text


def test_worker_runs_each_job_in_its_organisations_scope(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import worker

    seen: list[bool] = []
    monkeypatch.setattr(worker, "get_handler", lambda kind: lambda session, job: seen.append(
        is_synthetic()
    ))
    for synthetic in (True, False):
        org = make_org(db_session, synthetic=synthetic)
        job = enqueue(db_session, JobKind.DRAFT_ALL, {"actor": "test user"}, org_id=org.id)
        worker.run_job(db_session, job)
    assert seen == [True, False]
    assert not is_synthetic(), "the scope does not leak past the job"

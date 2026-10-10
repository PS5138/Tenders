"""The synthetic demo's specification through the real pipeline, offline.

Mirrors what the development seed builds for the demonstration business: the four library
documents ``frontend/scripts/seed-backend.ts`` uploads (not the held-out submission), confirmed,
then a tender with the question pack and the synthetic specification. The requirement scan runs
on the heuristic double and the fake embedder, exactly as in synthetic demo mode, and must give
the Specification tab a realistic spread: every requirement found once with its reference and
priority, nothing taken from the question pack or the introduction, and suggestions that are
Green for the requirements the library states, Amber or Green for those it partly covers, and
absent where the library says nothing.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from tests.e2e.conftest import EvalData
from tests.fakes.heuristic_llm import HeuristicFakeLLM

# frontend/scripts/seed-backend.ts LIBRARY_FILES
DEMO_LIBRARY = (
    "past_submission_westmoor_icb_2024.docx",
    "past_submission_harbourside_fT_2025.docx",
    "reference_iso27001_certificate_2024.docx",
    "reference_iso27001_certificate_2025.docx",
)


async def _upload_library(client: httpx.AsyncClient, eval_data: EvalData, run_jobs) -> None:  # noqa: ANN001
    ids = []
    for filename in DEMO_LIBRARY:
        with eval_data.path(filename).open("rb") as handle:
            response = await client.post("/documents", files={"file": (filename, handle.read())})
        assert response.status_code == 202, response.text
        ids.append(response.json()["id"])
        run_jobs()
    for document_id in ids:
        confirm = await client.post(f"/documents/{document_id}/confirm")
        assert confirm.status_code == 200, confirm.text


async def _tender_with_specification(
    client: httpx.AsyncClient, eval_data: EvalData, run_jobs  # noqa: ANN001
) -> str:
    pack = eval_data.document("question_pack")
    spec = eval_data.document("specification")
    created = await client.post(
        "/tenders",
        json={"name": "Northern Fells 2025 (synthetic)", "buyer": pack["expected"]["buyer"]},
    )
    tender_id = created.json()["id"]
    for entry, kind in ((pack, "question_pack"), (spec, "specification")):
        with eval_data.path(entry["filename"]).open("rb") as handle:
            response = await client.post(
                f"/tenders/{tender_id}/documents",
                data={"tender_doc_kind": kind},
                files={"file": (entry["filename"], handle.read())},
            )
        assert response.status_code == 202, response.text
        run_jobs()
    run_jobs()
    return tender_id


async def test_demo_specification_gives_a_realistic_spread(
    app_client: httpx.AsyncClient,
    db_session: Session,
    heuristic_llm: HeuristicFakeLLM,
    fake_embeddings,  # noqa: ANN001
    bound_sessions,  # noqa: ANN001
    run_jobs,  # noqa: ANN001
    eval_data: EvalData,
) -> None:
    await _upload_library(app_client, eval_data, run_jobs)
    tender_id = await _tender_with_specification(app_client, eval_data, run_jobs)

    body = (await app_client.get(f"/tenders/{tender_id}/requirements")).json()
    rows: list[dict[str, Any]] = body["requirements"]
    expected = eval_data.document("specification")["requirements"]

    # Every requirement once, in document order, with its reference, priority and exact text,
    # located in the buyer's document; nothing from the introduction or the question pack.
    assert [row["ref"] for row in rows] == [item["ref"] for item in expected]
    assert [row["priority"] for row in rows] == [item["priority"] for item in expected]
    assert [row["text"] for row in rows] == [item["text"] for item in expected]
    assert all(row["locator"]["start"] is not None for row in rows), "located in the document"

    # Suggestions: Green where the library states it, Amber or nothing where it covers part of
    # it, nothing where it says nothing. Never Red, and every suggestion carries evidence.
    by_ref = {row["ref"]: row for row in rows}
    for item in expected:
        row = by_ref[item["ref"]]
        suggestion = row["suggested_class"]
        if item["expected"] == "covered":
            assert suggestion == "A", (item["ref"], suggestion)
        elif item["expected"] == "partial":
            assert suggestion in ("B", None), (item["ref"], suggestion)
        else:
            assert suggestion is None, (item["ref"], suggestion)
        if suggestion is not None:
            assert row["suggestion"]["evidence"], item["ref"]
    suggestions = [row["suggested_class"] for row in rows]
    assert suggestions.count("B") >= 2, "the demo shows Amber suggestions as well as Green"
    assert suggestions.count(None) >= 3, "and requirements with no library evidence"
    assert body["summary"]["total"] == len(expected)
    assert body["summary"]["unrated"] == len(expected), "a suggestion is not a rating"

    # A rating survives a re-scan, and a re-scan neither duplicates nor reorders rows.
    rated = by_ref["3.1"]
    response = await app_client.patch(
        f"/requirements/{rated['id']}", json={"compliance_class": "A"}
    )
    assert response.status_code == 200, response.text
    rescan = await app_client.post(f"/tenders/{tender_id}/requirements/rescan")
    assert rescan.status_code in (200, 202), rescan.text
    run_jobs()
    again = (await app_client.get(f"/tenders/{tender_id}/requirements")).json()
    assert [row["id"] for row in again["requirements"]] == [row["id"] for row in rows]
    kept = next(row for row in again["requirements"] if row["id"] == rated["id"])
    assert kept["compliance_class"] == "A" and kept["rated_by"] == "test user"
    assert again["summary"]["green"] == 1 and again["summary"]["unrated"] == len(expected) - 1

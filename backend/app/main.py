"""FastAPI application and router registration."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    answers,
    documents,
    drafts,
    fixtures,
    jobs,
    library,
    questions,
    sections,
    tenders,
    threads,
)
from app.api.deps import ACTOR_HEADER, ORG_HEADER, actor_guard
from app.api.schemas import HealthResponse
from app.config import check_provider_configuration, get_settings

ROUTERS = (
    documents.router,
    sections.router,
    library.router,
    tenders.router,
    jobs.router,
    drafts.router,
    questions.router,
    answers.router,
    threads.router,
    fixtures.router,
)

# The front end is a separate Next.js application (plan: architecture) that calls the API with
# ``fetch`` from the browser and sends ``X-Actor`` on every write, so every browser call needs
# CORS and the writes need a preflight. Origins are an explicit list, never ``*``, so each
# environment can tighten them.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:3000",)

# Response headers the browser may read beyond the safelist: the duplicate-question-pack 409
# names the existing document only in ``X-Document-Id``, and an export fetched as a blob needs
# ``Content-Disposition`` for the filename.
CORS_EXPOSE_HEADERS: tuple[str, ...] = ("X-Document-Id", "Content-Disposition")


def cors_origins() -> list[str]:
    """Allowed browser origins from ``Settings.cors_origins`` (``CORS_ORIGINS``, comma-separated,
    never ``*``); blank entries are dropped and an empty setting falls back to the Next.js
    development origin."""
    raw = get_settings().cors_origins
    origins = [origin.strip() for origin in raw.split(",")]
    return [origin for origin in origins if origin] or list(DEFAULT_CORS_ORIGINS)


def create_app() -> FastAPI:
    # A real provider selected without its key is a start-up failure with a plain message, not
    # the first draft failing in a stream; the worker applies the same check in ``main``.
    check_provider_configuration(get_settings())
    app = FastAPI(
        title="Tenders API",
        version="0.1.0",
        description="Tender-response backend. Mutating requests carry an X-Actor header; "
        "X-Org-Id defaults to the seeded organisation.",
        dependencies=[Depends(actor_guard)],
    )
    # No cookies are used, so credentials stay off. Preflights are answered by the middleware
    # and never reach ``actor_guard``; the 400 for a missing X-Actor carries the CORS headers
    # so the front end can read the error body.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_methods=["*"],
        allow_headers=[ACTOR_HEADER, ORG_HEADER],
        expose_headers=list(CORS_EXPOSE_HEADERS),
    )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()

"""FastAPI application and router registration."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    answers,
    documents,
    drafts,
    fixtures,
    jobs,
    library,
    organisations,
    questions,
    sections,
    tenders,
    threads,
)
from app.api.deps import ACTOR_HEADER, ORG_HEADER, actor_guard
from app.api.documents import TOO_LARGE, upload_limit_message
from app.api.schemas import HealthResponse
from app.config import check_provider_configuration, get_settings

ROUTERS = (
    organisations.router,
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

# In the joined stack the Next.js server proxies every call and the browser never reaches this
# API, so CORS is not exercised there. It stays for direct browser use in development (the
# fixtures and a client pointed straight at the API). Origins are an explicit list, never ``*``.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:3000",)

# Response headers the browser may read beyond the safelist: the duplicate-question-pack 409
# names the existing document only in ``X-Document-Id``, and an export fetched as a blob needs
# ``Content-Disposition`` for the filename.
CORS_EXPOSE_HEADERS: tuple[str, ...] = ("X-Document-Id", "Content-Disposition")


# The two multipart upload routes. Only they carry a file, so only their bodies are bounded
# by ``UploadBodyLimit``; every other request passes through it untouched.
UPLOAD_ROUTES = re.compile(r"^/(documents|tenders/[0-9a-f-]{36}/documents)$", re.IGNORECASE)

# What a multipart body may carry beside the file itself: the boundaries, the part headers
# and the ``tender_doc_kind`` field. A body may exceed ``max_upload_bytes`` by at most this
# much before the request is cut off; ``read_upload`` still decides the file's own size
# precisely, so a file between the limit and the limit plus this allowance gets its 413 there.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class UploadBodyLimit:
    """Pure-ASGI guard that keeps an oversized upload from being spooled before the 413.

    FastAPI parses the whole multipart body (``request.form()``) before any dependency or the
    handler runs, and Starlette's ``max_part_size`` does not apply to file parts, so without
    the Next.js proxy in front (tests, the harness, curl, a future direct client) a caller
    could make the API write an arbitrarily large body to its temp directory before
    ``read_upload`` refuses it. This wrapper is scoped to ``POST /documents`` and
    ``POST /tenders/{id}/documents`` and applies ``Settings.max_upload_bytes`` plus
    ``MULTIPART_OVERHEAD_BYTES`` twice over:

    1. A declared ``Content-Length`` over the bound is answered 413 at once, without calling
       the application, so an honest client's oversized file costs no body bytes at all.
    2. Otherwise ``receive`` is wrapped to count ``http.request`` body bytes and, once they
       pass the bound, raises the 413 as an ``HTTPException`` from inside the parser, so a
       chunked body with no ``Content-Length`` is bounded too. FastAPI re-raises an
       ``HTTPException`` from the form parse unchanged and its handler renders the usual
       ``{detail}`` body; no further body chunk is read.

    The detail is ``upload_limit_message``, the same sentence ``read_upload`` and the proxy
    use. It is a plain ASGI class rather than ``@app.middleware("http")`` so the NDJSON
    streaming responses are never re-wrapped, and it is added first so it sits inside the
    service-secret guard and CORS: an unauthenticated caller still gets 401 first, and the
    413 carries the CORS headers.
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not UPLOAD_ROUTES.match(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return

        limit = get_settings().max_upload_bytes
        bound = limit + MULTIPART_OVERHEAD_BYTES
        detail = upload_limit_message(limit)

        declared = _content_length(scope)
        if declared is not None and declared > bound:
            await _send_json(send, TOO_LARGE, {"detail": detail})
            return

        seen = 0

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > bound:
                    raise HTTPException(TOO_LARGE, detail=detail)
            return message

        await self.app(scope, limited_receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_json(send: Send, status_code: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


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
        description="Tender-response backend. Service-authenticated: the Next.js server sets "
        "X-Actor and X-Org-Id on every request and the browser never calls this API directly. "
        "Without a proxy (tests, the harness, curl) X-Org-Id defaults to the seeded organisation.",
        dependencies=[Depends(actor_guard)],
    )

    # Added first, so it is the innermost middleware: inside the service-secret guard and
    # CORS below (``add_middleware`` prepends). See ``UploadBodyLimit``.
    app.add_middleware(UploadBodyLimit)

    @app.middleware("http")
    async def service_guard(request, call_next):
        secret = get_settings().service_secret
        if secret and not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), f"Bearer {secret}".encode()
        ):
            return JSONResponse(
                {"detail": "A valid service credential is required."}, status_code=401
            )
        return await call_next(request)

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
        settings = get_settings()
        return HealthResponse(
            status="ok",
            llm_provider=settings.llm_provider,
            embedding_provider=settings.embedding_provider,
            service_secret_enabled=bool(settings.service_secret),
            synthetic_demo=settings.synthetic_demo,
        )

    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()

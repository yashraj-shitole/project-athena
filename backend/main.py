"""Project Athena — FastAPI app entrypoint.

Run locally with:
    cd backend
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, auth, chat, connectors, documents, health, tools
from app.core.cache import close as close_cache
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security_headers import SecurityHeadersMiddleware
from app.services.llm.ollama import close_ollama
from app.services.orchestrator.llm_client import close_llm
from app.services.providers.health import start_probe, stop_probe


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    configure_logging()
    log = get_logger("athena")
    settings = get_settings()
    log.info(
        "app.start",
        env=settings.environment,
        model=settings.ollama_model,
        budget=settings.token_budget,
    )
    # Phase F: start the background health probe. The probe walks
    # every enabled connector and pings the upstream; the loop is
    # a single asyncio task (no apscheduler) that respects the
    # configured interval.
    await start_probe()
    try:
        yield
    finally:
        await stop_probe()
        await close_cache()
        await close_ollama()
        await close_llm()
        log.info("app.stop")


app = FastAPI(
    title="Project Athena",
    version="1.0.0",
    description="AI assistant with tool-calling, document intelligence, and "
    "short-context-aware orchestration.",
    lifespan=lifespan,
)

# ---- CORS ----
# H-21 — defense in depth. The validator in
# ``app.core.config._check_cors_origins`` already refuses ``*`` in the
# origin list. We additionally pin the methods/headers to a known
# allowlist (no ``*``) so a misconfigured origin list cannot escalate
# into a wildcard cross-origin capability grant.
_settings = get_settings()

# M-28 — security headers middleware. Adds HSTS, X-Content-Type-Options,
# X-Frame-Options, Referrer-Policy, Permissions-Policy, COOP / CORP / COEP,
# and a Content-Security-Policy. See ``app/core/security_headers.py``.
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    # Methods: the API uses GET (reads), POST (creates + non-stream
    # chat), PATCH (connector / tool updates), DELETE (connector /
    # conversation / document deletion), OPTIONS (CORS preflight).
    # We do not allow PUT, HEAD, TRACE, or CONNECT.
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    # Headers: the client sends ``Authorization`` (Bearer JWT),
    # ``Content-Type`` (JSON bodies), and ``Accept``. ``X-Requested-With``
    # is the common CSRF guard header for SPAs; accepting it does not
    # weaken anything because it is just a marker.
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Requested-With",
    ],
    # ``Vary: Origin`` is set automatically by Starlette when an
    # origin matches; we add ``Vary: Accept`` for SSE endpoints (see
    # M-23) — those set the header inline in the route.
    expose_headers=["Content-Length", "Content-Type"],
)

# ---- Routers ----
app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(connectors.router)  # /api/connectors/*
app.include_router(health.router)  # /health, /model (no /api prefix — matches nginx)
# Re-mount under /api so /api/model, /api/health, /api/metrics work for
# clients that prefix every API call with /api (the default for the
# frontend's apiClient). Routes are path-relative (e.g. @router.get("/model"))
# so they pick up the prefix transparently.
app.include_router(health.router, prefix="/api")


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "name": "Project Athena",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/health",
        }
    )

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

from app.api import auth, chat, connectors, documents, health, tools
from app.core.cache import close as close_cache
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
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
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Routers ----
app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(connectors.router)  # /api/connectors/*
app.include_router(health.router)  # /health, /model (no /api prefix — matches nginx)


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

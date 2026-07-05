"""Health check + active model info + metrics (FR-37, FR-39, FR-36)."""
from __future__ import annotations

import time

from fastapi import APIRouter
from sqlalchemy import text

from app.api.dependencies import AdminUser
from app.core.cache import HITS, MISSES, get_client
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.ollama import get_ollama

router = APIRouter(tags=["health"])
log = get_logger(__name__)
_settings = get_settings()


@router.get("/health")
async def health() -> dict:
    out: dict = {"status": "ok", "checks": {}}
    # DB
    t0 = time.perf_counter()
    try:
        from app.core.database import SessionLocal

        async with SessionLocal() as s:
            await s.execute(text("SELECT 1"))
        out["checks"]["db"] = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001
        out["status"] = "degraded"
        out["checks"]["db"] = {"ok": False, "error": str(exc)}

    # Redis
    t0 = time.perf_counter()
    try:
        pong = await get_client().ping()
        out["checks"]["redis"] = {
            "ok": bool(pong),
            "ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        out["status"] = "degraded"
        out["checks"]["redis"] = {"ok": False, "error": str(exc)}

    # LLM (Ollama)
    t0 = time.perf_counter()
    try:
        client = get_ollama()
        # Cheap probe: ask /api/tags
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as h:
            r = await h.get(f"{_settings.ollama_url.rstrip('/')}/api/tags")
        out["checks"]["llm"] = {
            "ok": r.status_code == 200,
            "ms": int((time.perf_counter() - t0) * 1000),
            "model": _settings.ollama_model,
        }
        if r.status_code != 200:
            out["status"] = "degraded"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "degraded"
        out["checks"]["llm"] = {"ok": False, "error": str(exc), "model": _settings.ollama_model}

    return out


@router.get("/model")
async def active_model() -> dict:
    """FR-39: model + provider + budget in effect."""
    return {
        "model": _settings.ollama_model,
        "provider": "ollama",
        "base_url": _settings.ollama_url,
        "context_budget": _settings.token_budget,
        "embedding_model": _settings.embedding_model_name,
        "embedding_dim": _settings.embedding_dim,
    }


@router.get("/metrics")
async def metrics(_admin: AdminUser) -> dict:
    """FR-36: cache hit/miss counters (best-effort; Redis may be down).

    Admin-gated: the counters and any future operational internals here
    are not for unauthenticated callers. `/health` (liveness) stays open;
    `/metrics` does not.
    """
    out: dict[str, int] = {"hits": 0, "misses": 0}
    try:
        c = get_client()
        v = await c.get(HITS)
        if v is not None:
            out["hits"] = int(v)
        v = await c.get(MISSES)
        if v is not None:
            out["misses"] = int(v)
    except Exception:  # noqa: BLE001
        pass
    total = out["hits"] + out["misses"]
    out["hit_rate"] = (out["hits"] / total) if total else 0.0
    out["total"] = total
    return out

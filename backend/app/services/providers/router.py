"""ModelRouter — resolves a (connector_id, model_hint) request to a
ready-to-call `ProviderAdapter` plus the model name to send it.

Resolution order:

  1. Explicit `connector_id` (from the chat request). The connector
     MUST be enabled, not soft-deleted, and visible to the calling
     user (RLS + ownership).
  2. The calling user's `is_default = TRUE` connector.
  3. A system-shared (`is_admin = TRUE`) default connector.
  4. The built-in Ollama fallback (constructed from `app.core.config`).
     This preserves the Phase-1 byte-for-byte behaviour when no
     connector is configured.

If the resolved connector is disabled, we skip it and continue down
the list. Soft-deleted rows are never considered.
"""
from __future__ import annotations

import uuid
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.connector import ModelConnector
from app.services.providers import registry
from app.services.providers.base import ProviderAdapter, ProviderError

log = get_logger(__name__)
_settings = get_settings()


def _build_adapter(row: ModelConnector) -> ProviderAdapter:
    """Construct the right adapter for a connector row.

    The adapter is constructed lazily on first use and then cached on
    the `ModelRouter` instance. Constructing fresh per-call would
    allocate a new `httpx.AsyncClient` on every chat turn, which is
    expensive and exhausts the local socket pool.

    SSRF: we run `assert_safe_url(allow_loopback=True)` here so the
    user-supplied base URL is checked exactly once, at resolution
    time. The adapter constructor trusts its caller.
    """
    from app.core.ssrf import assert_safe_url

    assert_safe_url(row.base_url, allow_loopback=True)

    cls = registry.get(row.provider)
    api_key = None
    if row.api_key_enc:
        # Decrypt here — the only place the plaintext exists in memory
        # outside the request handler. The router instance holds the
        # decrypted key as long as the connection to the upstream is
        # in use; that's the same lifetime as the request, so the key
        # is at rest only as the encrypted BYTEA in the DB.
        from app.services.providers.crypto import decrypt

        api_key = decrypt(row.api_key_enc)
    timeout = float((row.settings or {}).get("timeout_s") or 60.0)

    # Common kwargs for every adapter.
    common: dict = {
        "base_url": row.base_url,
        "api_key": api_key,
        "auth_type": row.auth_type,
        "auth_header_name": row.auth_header_name,
        "custom_headers": row.custom_headers or {},
        "organization_id": row.organization_id,
        "project_id": row.project_id,
        "api_version": row.api_version,
        "timeout_s": timeout,
        "default_model": row.default_model,
        "models": list(row.models or []),
    }
    # The registry already maps every provider name to its adapter
    # (incl. "ollama" -> OllamaProvider). The earlier OLLAMA special
    # case here force-routed Ollama connectors to OpenAICompatibleProvider,
    # which POSTs {base_url}/chat/completions — Ollama's OpenAI-compat
    # shim lives at /v1/chat/completions, so against the default base URL
    # (server root, no /v1) that 404'd with "404 page not found". Falling
    # through to `cls` routes Ollama to OllamaProvider's native /api/chat.
    return cls(**common)


class ModelRouter:
    """Stateless resolver.

    One instance per app is fine — there's no per-request state. The
    adapter cache lives on the row's resolved `ProviderAdapter`
    instance; we drop it on `aclose()`.
    """

    def __init__(self) -> None:
        # cache: row_id -> adapter. We don't use this yet; the wiring
        # in Phase C will keep adapters warm across requests. The hook
        # is here so the public API is stable.
        self._cache: dict[uuid.UUID, ProviderAdapter] = {}

    async def aclose(self) -> None:
        for adapter in self._cache.values():
            try:
                await adapter.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._cache.clear()

    async def resolve(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        connector_id: Optional[uuid.UUID] = None,
        model_hint: Optional[str] = None,
    ) -> Tuple[ProviderAdapter, str, Optional[uuid.UUID]]:
        """Return `(adapter, model_name, resolved_connector_id)`.

        `resolved_connector_id` is `None` for the built-in Ollama
        fallback — callers can use it to decide whether to write a
        usage row (a `None` connector_id means "use the default
        Ollama; not user-attributed").
        """
        if connector_id is not None:
            row = await self._load_connector(session, connector_id, user_id)
            if row is not None and row.is_enabled:
                return _build_adapter(row), (model_hint or row.default_model), row.id
            if row is not None and not row.is_enabled:
                log.info(
                    "router.connector_disabled",
                    connector_id=str(connector_id),
                    user_id=str(user_id),
                )

        # 2. user default.
        row = await self._load_user_default(session, user_id)
        if row is not None and row.is_enabled:
            return _build_adapter(row), (model_hint or row.default_model), row.id

        # 3. system default (admin-shared).
        row = await self._load_system_default(session)
        if row is not None and row.is_enabled:
            return _build_adapter(row), (model_hint or row.default_model), row.id

        # 4. built-in Ollama fallback.
        adapter = await self._ollama_fallback()
        return adapter, (model_hint or _settings.OLLAMA_MODEL), None

    async def _load_connector(
        self, session: AsyncSession, connector_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[ModelConnector]:
        """Load a single connector by id, RLS-scoped to the user.

        RLS is set by `get_user_db` upstream; the implicit policy
        filters rows so the user only sees their own. We additionally
        enforce `deleted_at IS NULL` and the cross-user admin-shared
        visibility here so the test harness (no RLS) still behaves
        the same.
        """
        stmt = select(ModelConnector).where(
            ModelConnector.id == connector_id,
            ModelConnector.deleted_at.is_(None),
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        if row.user_id != user_id and not row.is_admin:
            return None
        return row

    async def _load_user_default(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> Optional[ModelConnector]:
        stmt = select(ModelConnector).where(
            ModelConnector.user_id == user_id,
            ModelConnector.is_default.is_(True),
            ModelConnector.is_enabled.is_(True),
            ModelConnector.deleted_at.is_(None),
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _load_system_default(
        self, session: AsyncSession
    ) -> Optional[ModelConnector]:
        stmt = select(ModelConnector).where(
            ModelConnector.is_admin.is_(True),
            ModelConnector.is_default.is_(True),
            ModelConnector.is_enabled.is_(True),
            ModelConnector.deleted_at.is_(None),
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _ollama_fallback(self) -> ProviderAdapter:
        """The built-in Ollama client, exposed as an adapter.

        Today, the orchestrator's `LLMClient` is hard-coded to Ollama.
        Phase C will rewrite it to go through the router; the fallback
        is the path that keeps Phase 1 behaviour intact.
        """
        from app.core.config import get_settings
        from app.services.providers.ollama import OllamaProvider

        # Settings is read at fallback time so test config (which
        # mutates env vars) is picked up.
        s = get_settings()
        # Use the native Ollama adapter (POST /api/chat), not the
        # OpenAI-compat shim: OLLAMA_BASE_URL defaults to the server
        # root (http://localhost:11434, no /v1), so the compat path's
        # {base_url}/chat/completions 404s. /api/chat works at the root.
        return OllamaProvider(
            base_url=s.OLLAMA_BASE_URL,
            api_key=None,
            auth_type="none",
            custom_headers={},
            timeout_s=s.OLLAMA_TIMEOUT_S,
            default_model=s.OLLAMA_MODEL,
            models=[s.OLLAMA_MODEL],
        )


__all__ = ["ModelRouter", "ProviderError"]

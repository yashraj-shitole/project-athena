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
from datetime import datetime
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
    adapter cache keeps one warm `ProviderAdapter` (and its pooled
    `httpx.AsyncClient`) per connector so we don't allocate a fresh
    client on every chat turn. Cache entries are keyed on
    `(row.id, row.updated_at)`; mutating the connector bumps
    `updated_at` (via `onupdate=func.now()`), so a rotated API key or
    changed base URL invalidates the entry on the next resolve and the
    stale adapter is closed. We drop everything on `aclose()`.
    """

    def __init__(self) -> None:
        # row_id -> (updated_at, adapter). The updated_at fingerprint
        # is what makes the cache safe across connector edits.
        self._cache: dict[uuid.UUID, tuple[datetime, ProviderAdapter]] = {}
        # The built-in Ollama fallback has no DB row; cache it separately
        # so the common Phase-1 path doesn't allocate a client per turn.
        self._fallback_adapter: Optional[ProviderAdapter] = None

    async def aclose(self) -> None:
        for _ts, adapter in self._cache.values():
            try:
                await adapter.aclose()
            except Exception:  # noqa: BLE001
                pass
        if self._fallback_adapter is not None:
            try:
                await self._fallback_adapter.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._fallback_adapter = None
        self._cache.clear()

    async def _cached_adapter(self, row: ModelConnector) -> ProviderAdapter:
        """Return a warm adapter for `row`, rebuilding it if the row's
        `updated_at` fingerprint changed since it was cached."""
        cached = self._cache.get(row.id)
        if cached is not None and cached[0] == row.updated_at:
            return cached[1]
        # Stale or missing — close the old adapter (releases its pooled
        # httpx connection) before we drop the reference.
        if cached is not None:
            try:
                await cached[1].aclose()
            except Exception:  # noqa: BLE001
                pass
        adapter = _build_adapter(row)
        self._cache[row.id] = (row.updated_at, adapter)
        return adapter

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
                return (
                    await self._cached_adapter(row),
                    (model_hint or row.default_model),
                    row.id,
                )
            if row is not None and not row.is_enabled:
                log.info(
                    "router.connector_disabled",
                    connector_id=str(connector_id),
                    user_id=str(user_id),
                )

        # 2. user default.
        row = await self._load_user_default(session, user_id)
        if row is not None and row.is_enabled:
            return (
                await self._cached_adapter(row),
                (model_hint or row.default_model),
                row.id,
            )

        # 3. system default (admin-shared).
        row = await self._load_system_default(session)
        if row is not None and row.is_enabled:
            return (
                await self._cached_adapter(row),
                (model_hint or row.default_model),
                row.id,
            )

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

        Cached on the router so the common Phase-1 path (no connector
        configured) doesn't allocate a new `httpx.AsyncClient` on every
        chat turn.
        """
        if self._fallback_adapter is not None:
            return self._fallback_adapter
        from app.core.config import get_settings
        from app.services.providers.ollama import OllamaProvider

        # Settings is read at fallback time so test config (which
        # mutates env vars) is picked up on first use.
        s = get_settings()
        # Use the native Ollama adapter (POST /api/chat), not the
        # OpenAI-compat shim: OLLAMA_BASE_URL defaults to the server
        # root (http://localhost:11434, no /v1), so the compat path's
        # {base_url}/chat/completions 404s. /api/chat works at the root.
        adapter = OllamaProvider(
            base_url=s.OLLAMA_BASE_URL,
            api_key=None,
            auth_type="none",
            custom_headers={},
            timeout_s=s.OLLAMA_TIMEOUT_S,
            default_model=s.OLLAMA_MODEL,
            models=[s.OLLAMA_MODEL],
        )
        self._fallback_adapter = adapter
        return adapter


__all__ = ["ModelRouter", "ProviderError"]

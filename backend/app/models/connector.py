"""External Model Connector ORM models.

The user-facing `ModelConnector` row holds everything a provider adapter
needs to talk to an external LLM service — base URL, encrypted API key,
model list, capabilities, settings. `api_key_enc` is bytes encrypted by
`app.services.providers.crypto`; decryption is server-side only and the
plaintext NEVER leaves the API.

Soft delete: `deleted_at` is non-null on delete; every query must filter
`deleted_at IS NULL` (helpers in `app.services.providers` enforce it).

The `ConnectorAuditLog` and `ConnectorUsage` models back the audit +
usage dashboards. They are append-only and use a different (write-mostly)
shape.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# Allowed values for `ModelConnector.provider`. Kept here so the enum is
# discoverable from one place — adapters and the Pydantic schema import
# from here. Add a new value alongside a new adapter file in
# `app/services/providers/`.
PROVIDER_OPENAI_COMPAT = "openai_compat"  # OpenAI-compatible HTTP API
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GEMINI = "gemini"
PROVIDER_AZURE_OPENAI = "azure_openai"
PROVIDER_OLLAMA = "ollama"
PROVIDER_CUSTOM = "custom"
PROVIDERS: tuple[str, ...] = (
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_ANTHROPIC,
    PROVIDER_GEMINI,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_OLLAMA,
    PROVIDER_CUSTOM,
)

# Allowed values for `ModelConnector.auth_type`. OAuth is reserved for
# Phase 2 (it requires a token-refresh dance the current single-shot
# request flow doesn't support).
AUTH_BEARER = "bearer"
AUTH_HEADER = "header"  # e.g. `x-api-key: <key>`
AUTH_BASIC = "basic"
AUTH_NONE = "none"
AUTH_TYPES: tuple[str, ...] = (AUTH_BEARER, AUTH_HEADER, AUTH_BASIC, AUTH_NONE, "oauth")


class ModelConnector(Base):
    __tablename__ = "model_connectors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Owner. NULL is reserved for future system connectors (managed by
    # a privileged service account); today every row has a non-null
    # user_id at create time.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    # Fernet-encrypted ciphertext (bytes). Nullable for connectors that
    # do not need a key (e.g. local Ollama with no auth).
    api_key_enc: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    # Plain preview ("sk-…1234") for UI display. NEVER returns the real
    # key — see ModelConnectorPublic schema.
    api_key_preview: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    auth_type: Mapped[str] = mapped_column(
        String, nullable=False, default=AUTH_BEARER
    )
    # Header name for `auth_type = header` (e.g. `x-api-key`).
    auth_header_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Azure uses this; ignored by other adapters.
    api_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Free-form extra headers (e.g. Anthropic's `anthropic-version`).
    custom_headers: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Default model to use when the user does not pick one explicitly.
    default_model: Mapped[str] = mapped_column(String, nullable=False)
    # The models this connector exposes. Stored as JSONB (not ARRAY) so
    # the list can later carry per-model metadata (e.g. context window)
    # without a schema change.
    models: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Capability flags. Stored as JSONB so we can add new flags without
    # a migration. Shape: {chat, stream, tools, vision, embeddings,
    # json_mode, structured_output, audio_in, audio_out, image_in,
    # image_out}
    capabilities: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text(
            "'{\"chat\": true, \"stream\": true, \"tools\": false, "
            "\"vision\": false, \"embeddings\": false, "
            "\"json_mode\": false, \"structured_output\": false}'::jsonb"
        )
    )
    # Per-connector knobs the adapter reads. Shape: {temperature, top_p,
    # max_tokens, frequency_penalty, presence_penalty, seed, stop,
    # timeout_s, retry_max, retry_backoff_s, rate_limit_rpm,
    # rate_limit_tpm, json_mode, system_prompt}
    settings: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Admin kill switch + user defaults.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # System-shared row, visible to every user. Only admins may set this.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # UI-only metadata.
    group_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Stored as JSONB (list[str]) rather than ARRAY so the unit-test
    # suite can run on SQLite; production (Postgres) reads/writes the
    # same JSONB column identically.
    tags: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Last known health snapshot (cheap read for the UI badge).
    last_health: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_health_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_latency_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    # Circuit breaker. Reset to 0 on the next successful probe.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Cached discovered model list (probed on demand + on a schedule).
    discovered_models: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    discovered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Timestamps + soft delete.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Name is unique per (user, soft-delete bucket). The partial
        # index is created in init.sql — this UniqueConstraint is for
        # SQLAlchemy's metadata; the partial-WHERE version is the one
        # Postgres enforces.
        UniqueConstraint("user_id", "name", name="uq_connectors_user_name"),
        Index("idx_connectors_user", "user_id"),
        Index("idx_connectors_enabled", "is_enabled"),
        Index("idx_connectors_provider", "provider"),
    )


class ConnectorAuditLog(Base):
    """Append-only audit trail for every mutation / sensitive read.

    `before_redacted` / `after_redacted` are JSON dumps of the public
    Pydantic schema — which never carries the encrypted blob, so
    leakage is impossible by construction.
    """

    __tablename__ = "connector_audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_connectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    before_redacted: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    after_redacted: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_audit_connector_at", "connector_id", "at"),)


class ConnectorUsage(Base):
    """One row per request that hit a connector.

    Kept narrow (12 columns) so the usage dashboard can aggregate over
    weeks without a covering index. `error_class` is one of the strings
    the `ProviderError` taxonomy yields (see app/services/providers/errors.py).
    """

    __tablename__ = "connector_usage"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_connectors.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String, nullable=False)
    # Token counts. Best-effort — providers that don't return them
    # simply store 0.
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "ok" | "error" | "timeout" | "rate_limited" | "auth_failed" |
    # "cancelled" | "stream_interrupted"
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_class: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Optional — derived from the connector's settings if the provider
    # publishes a price table; otherwise 0.
    cost_estimate: Mapped[float] = mapped_column(
        # Numeric gives us exact decimals. Map as String for cross-DB
        # portability (we read with Decimal() on the way out).
        String, nullable=False, default="0"
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_usage_connector_at", "connector_id", "at"),
        Index("idx_usage_user_at", "user_id", "at"),
    )


__all__ = [
    "ModelConnector",
    "ConnectorAuditLog",
    "ConnectorUsage",
    "PROVIDERS",
    "PROVIDER_OPENAI_COMPAT",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_GEMINI",
    "PROVIDER_AZURE_OPENAI",
    "PROVIDER_OLLAMA",
    "PROVIDER_CUSTOM",
    "AUTH_BEARER",
    "AUTH_HEADER",
    "AUTH_BASIC",
    "AUTH_NONE",
    "AUTH_TYPES",
]

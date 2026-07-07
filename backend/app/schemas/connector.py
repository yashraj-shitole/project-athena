"""Pydantic schemas for the External Model Connectors REST API.

Three shapes:

* `ModelConnectorCreate` — what the user POSTs. Includes the
  plaintext `api_key` (if any). The route encrypts it and never
  echoes it back.
* `ModelConnectorUpdate` — PATCH. `api_key` may be empty (= "no
  change") or absent (= "no change") or non-empty (= replace).
* `ModelConnectorPublic` — what the user GETs. NEVER carries the
  encrypted blob; the `api_key_preview` is the only key-shaped
  field, and it's pre-computed at write time.

The Pydantic shape mirrors the `ModelConnector` ORM row but strips
the encryption / server-side fields. The route layer is responsible
for encryption, preview generation, and JSONB-encoding the
`tags` / `models` / `settings` / `capabilities` / `custom_headers`
columns.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import Field, field_validator

from app.models.connector import AUTH_TYPES, PROVIDERS
from app.schemas.base import ORMModelBase, RequestBase


class ModelConnectorCreate(RequestBase):
    """Request body for `POST /api/connectors`.

    Security notes
    --------------

    * ``is_admin=True`` is **always** accepted by the schema — the
      route layer is the only place that knows whether the caller
      is an admin. We don't push that policy into the schema because
      the schema is shared with the test-suite (which exercises the
      admin path explicitly). See ``app/api/connectors.py::create_connector``
      for the privilege check.
    * ``extra="forbid"`` is inherited from :class:`RequestBase` — a
      payload containing a field this schema does not declare (e.g.
      ``user_id``) is rejected with a 422 before reaching the route.
    """

    name: str = Field(min_length=1, max_length=120)
    provider: str
    base_url: str = Field(min_length=1, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=2000)
    auth_type: str = "bearer"
    auth_header_name: Optional[str] = Field(default=None, max_length=120)
    organization_id: Optional[str] = Field(default=None, max_length=120)
    project_id: Optional[str] = Field(default=None, max_length=120)
    api_version: Optional[str] = Field(default=None, max_length=60)
    custom_headers: dict[str, str] = Field(default_factory=dict)
    default_model: str = Field(min_length=1, max_length=200)
    models: List[str] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(
        default_factory=lambda: {
            "chat": True,
            "stream": True,
            "tools": False,
            "vision": False,
            "embeddings": False,
            "json_mode": False,
            "structured_output": False,
        }
    )
    settings: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    is_default: bool = False
    # Only admin users may set `is_admin=True`. The route enforces
    # this with the AdminUser dep.
    is_admin: bool = False
    group_name: Optional[str] = Field(default=None, max_length=120)
    tags: List[str] = Field(default_factory=list)
    is_favorite: bool = False

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        if v not in PROVIDERS:
            raise ValueError(
                f"unknown provider {v!r}; expected one of {PROVIDERS}"
            )
        return v

    @field_validator("auth_type")
    @classmethod
    def _check_auth_type(cls, v: str) -> str:
        if v not in AUTH_TYPES:
            raise ValueError(
                f"unknown auth_type {v!r}; expected one of {AUTH_TYPES}"
            )
        return v

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v


class ModelConnectorUpdate(RequestBase):
    """Request body for `PATCH /api/connectors/{id}`.

    Every field is optional. To rotate an API key, pass a non-empty
    `api_key`. To keep the existing key, omit the field or pass an
    empty string. There is no way to *clear* a key via this shape
    (a user with a key can rotate, but never accidentally wipe).

    Security notes
    --------------

    * ``is_admin`` is **deliberately absent** from this schema. The
      only way to make a connector admin-shared is via the create
      path, and only when the caller is in the admin allowlist.
      PATCHing an existing connector to flip ``is_admin`` would
      otherwise be a one-request privilege escalation — the
      ``connectors_iso`` RLS policy is ``user_id = me OR is_admin``,
      so flipping the flag promotes the row into the global
      visibility set. The fix is to remove the lever from the
      schema (this file) **and** the PATCH mass-assignment loop
      (``app/api/connectors.py::update_connector``).
    * ``extra="forbid"`` is inherited from :class:`RequestBase`.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    base_url: Optional[str] = Field(default=None, min_length=1, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=2000)
    auth_type: Optional[str] = None
    auth_header_name: Optional[str] = Field(default=None, max_length=120)
    organization_id: Optional[str] = Field(default=None, max_length=120)
    project_id: Optional[str] = Field(default=None, max_length=120)
    api_version: Optional[str] = Field(default=None, max_length=60)
    custom_headers: Optional[dict[str, str]] = None
    default_model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    models: Optional[List[str]] = None
    capabilities: Optional[dict[str, bool]] = None
    settings: Optional[dict[str, Any]] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    # ``is_admin`` removed: see class docstring. Promotion to
    # admin-shared is create-only and admin-gated.
    group_name: Optional[str] = Field(default=None, max_length=120)
    tags: Optional[List[str]] = None
    is_favorite: Optional[bool] = None

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v

    @field_validator("auth_type")
    @classmethod
    def _check_auth_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in AUTH_TYPES:
            raise ValueError(
                f"unknown auth_type {v!r}; expected one of {AUTH_TYPES}"
            )
        return v


class ModelConnectorPublic(ORMModelBase):
    """Response shape. NEVER carries `api_key` or `api_key_enc`."""

    id: uuid.UUID
    name: str
    provider: str
    base_url: str
    api_key_preview: Optional[str] = None
    auth_type: str
    auth_header_name: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    api_version: Optional[str] = None
    custom_headers: dict[str, str] = Field(default_factory=dict)
    default_model: str
    models: List[str] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool
    is_default: bool
    is_admin: bool
    group_name: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_favorite: bool
    last_health: Optional[str] = None
    last_health_at: Optional[datetime] = None
    last_health_latency_ms: Optional[int] = None
    consecutive_failures: int = 0
    discovered_models: List[str] = Field(default_factory=list)
    discovered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # `is_owner` lets the UI hide admin-shared rows from "delete"
    # actions even though they're returned in the list.
    is_owner: bool = True


class ConnectorTemplate(ORMModelBase):
    """Canned `provider`+`default_base_url` per provider type.

    Returned by `GET /api/connectors/templates` so the UI can
    pre-populate a sensible base URL when the user picks a
    provider from the dropdown.
    """

    provider: str
    name: str
    base_url: str
    auth_type: str
    notes: Optional[str] = None


class ConnectorListResponse(ORMModelBase):
    connectors: List[ModelConnectorPublic]
    templates: List[ConnectorTemplate] = Field(default_factory=list)


class SetDefaultResponse(ORMModelBase):
    id: uuid.UUID
    is_default: bool


class HealthCheckResult(ORMModelBase):
    ok: bool
    latency_ms: int = 0
    status: str = "unknown"
    capabilities: dict[str, bool] = Field(default_factory=dict)
    models: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    category: str = "unknown"
    status_code: Optional[int] = None


class TestRequest(RequestBase):
    """Test a connector without saving it (POST /api/connectors/test).

    ``extra="forbid"`` is inherited from :class:`RequestBase` so a
    payload smuggling ``is_admin`` (or any other field the server
    does not declare) is rejected before the route runs.
    """

    name: Optional[str] = "preview"
    provider: str
    base_url: str
    api_key: Optional[str] = None
    auth_type: str = "bearer"
    auth_header_name: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    api_version: Optional[str] = None
    custom_headers: dict[str, str] = Field(default_factory=dict)
    default_model: str = ""
    models: List[str] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 8.0


__all__ = [
    "ModelConnectorCreate",
    "ModelConnectorUpdate",
    "ModelConnectorPublic",
    "ConnectorTemplate",
    "ConnectorListResponse",
    "SetDefaultResponse",
    "HealthCheckResult",
    "TestRequest",
]

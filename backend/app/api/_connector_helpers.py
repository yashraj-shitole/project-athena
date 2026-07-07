"""Helpers shared by the connector routers.

The public schema (`ModelConnectorPublic`) must NEVER carry the
encrypted `api_key_enc` field. This module is the only place that
turns an ORM `ModelConnector` row into the public shape, so we can
audit "no plaintext key ever leaves the API" in one place.
"""
from __future__ import annotations

import uuid
from typing import Optional

from app.models.connector import ModelConnector
from app.schemas.connector import ModelConnectorPublic


def to_public(row: ModelConnector, *, is_owner: Optional[bool] = None) -> ModelConnectorPublic:
    """Convert an ORM row to the public Pydantic schema.

    `is_owner` is the only computed field — it lets the UI hide
    admin-shared rows from owner-only actions. When `None`, the
    helper assumes "the caller is the owner" (the safest default;
    routers that surface shared rows should set it explicitly).
    """
    if is_owner is None:
        # `created_by` is a field on the row? No — `user_id` is.
        # We have no request context here, so we set True and let
        # the caller override if needed. The default works for
        # list/get operations where the router already filtered
        # to caller-owned rows.
        is_owner = True
    return ModelConnectorPublic(
        id=row.id,
        name=row.name,
        provider=row.provider,
        base_url=row.base_url,
        api_key_preview=row.api_key_preview,
        auth_type=row.auth_type,
        auth_header_name=row.auth_header_name,
        organization_id=row.organization_id,
        project_id=row.project_id,
        api_version=row.api_version,
        custom_headers=row.custom_headers or {},
        default_model=row.default_model,
        models=list(row.models or []),
        capabilities=row.capabilities or {},
        settings=row.settings or {},
        is_enabled=row.is_enabled,
        is_default=row.is_default,
        is_admin=row.is_admin,
        group_name=row.group_name,
        tags=list(row.tags or []),
        is_favorite=row.is_favorite,
        last_health=row.last_health,
        last_health_at=row.last_health_at,
        last_health_latency_ms=row.last_health_latency_ms,
        consecutive_failures=row.consecutive_failures,
        discovered_models=list(row.discovered_models or []),
        discovered_at=row.discovered_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        is_owner=is_owner,
    )


__all__ = ["to_public"]

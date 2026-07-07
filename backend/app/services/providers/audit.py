"""Append audit log rows for connector mutations and sensitive reads.

`before_redacted` / `after_redacted` MUST be the public Pydantic schema
dump, which never carries `api_key_enc` (the field does not exist on
`ModelConnectorPublic`). The dump is JSON-safe; we JSON-serialize here
so the column type stays JSONB without requiring callers to.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.connector import ConnectorAuditLog

log = get_logger(__name__)

# Stable action vocabulary. The dashboard's filter UI uses these
# strings verbatim, so do not rename without updating the frontend.
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_SET_DEFAULT = "set_default"
ACTION_TEST = "test"
ACTION_REFRESH_MODELS = "refresh_models"
ACTION_CLONE = "clone"
ACTIONS: tuple[str, ...] = (
    ACTION_CREATE,
    ACTION_UPDATE,
    ACTION_DELETE,
    ACTION_SET_DEFAULT,
    ACTION_TEST,
    ACTION_REFRESH_MODELS,
    ACTION_CLONE,
)


async def record(
    session: AsyncSession,
    *,
    connector_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> ConnectorAuditLog:
    """Write one audit row. Caller is responsible for `session.commit()`.

    `before` / `after` should already be the redacted dict (the Pydantic
    public schema dump). We accept `None` for events that have no
    before/after (e.g. `test`).
    """
    if action not in ACTIONS:
        # Internal guard — the DB will accept any string, but a typo
        # here would silently corrupt the dashboard's filter.
        raise ValueError(
            f"unknown audit action: {action!r} (expected one of {ACTIONS})"
        )
    row = ConnectorAuditLog(
        connector_id=connector_id,
        user_id=user_id,
        action=action,
        before_redacted=before,
        after_redacted=after,
        ip=(ip or "")[:64] or None,
        user_agent=(user_agent or "")[:500] or None,
    )
    session.add(row)
    log.info(
        "connector.audit",
        connector_id=str(connector_id),
        user_id=str(user_id),
        action=action,
    )
    return row


__all__ = ["record", "ACTIONS"] + list(ACTIONS)

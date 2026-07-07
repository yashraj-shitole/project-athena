"""Regression tests for H-22 — request schemas reject unknown fields.

Every request body the API accepts (POST /api/auth/register,
POST /api/chat, POST /api/conversations, POST /api/tools, …) is
parsed by a Pydantic model. We rely on ``extra="forbid"`` to prevent
mass-assignment: a payload containing a field the schema does not
declare must be rejected with a 422, not silently swallowed.

These tests cover the canonical "smuggle a privileged field" attacks
that motivated H-22:

* ``is_admin=True`` on connector / tool creation (C-2)
* ``user_id`` on chat (privilege confusion)
* ``is_active=False`` on user creation (avoid lockout flow)
* ``id`` on conversation (cross-tenant write)

A passing test means the schema refuses the unknown field. A
regression to a permissive ``ORMModelBase`` would let the smoke
through and the test would fail loud.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError


def test_user_create_rejects_is_active():
    """A new account must not be able to set ``is_active=False`` to
    avoid the admin-disable flow.
    """
    from app.schemas.auth import UserCreate

    with pytest.raises(ValidationError):
        UserCreate(
            email="attacker@example.com",
            password="Sup3rSecret!",
            is_active=False,
        )


def test_user_create_rejects_is_admin():
    """H-22 — no request schema accepts ``is_admin``. The promotion
    is route-gated; smuggling it in a body must fail loud.
    """
    from app.schemas.auth import UserCreate

    with pytest.raises(ValidationError):
        UserCreate(
            email="attacker@example.com",
            password="Sup3rSecret!",
            is_admin=True,
        )


def test_user_login_rejects_unknown_fields():
    from app.schemas.auth import UserLogin

    with pytest.raises(ValidationError):
        UserLogin(
            email="x@example.com",
            password="Sup3rSecret!",
            is_active=True,
        )


def test_refresh_request_rejects_unknown_fields():
    from app.schemas.auth import RefreshRequest

    with pytest.raises(ValidationError):
        RefreshRequest(
            refresh_token="abc",
            token_version=9999,  # would otherwise let an attacker pin a future token
        )


def test_chat_request_rejects_user_id():
    """A chat payload must not be able to set the principal."""
    from app.schemas.conversation import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(
            message="hello",
            user_id=str(uuid.uuid4()),
        )


def test_chat_request_rejects_is_admin():
    from app.schemas.conversation import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(
            message="hello",
            is_admin=True,
        )


def test_conversation_create_rejects_user_id():
    from app.schemas.conversation import ConversationCreate

    with pytest.raises(ValidationError):
        ConversationCreate(title="hi", user_id=str(uuid.uuid4()))


def test_conversation_create_rejects_id():
    """A new conversation cannot pre-assign its own id — that would
    let a caller cross-write into an existing tenant.
    """
    from app.schemas.conversation import ConversationCreate

    with pytest.raises(ValidationError):
        ConversationCreate(
            title="hi",
            id=str(uuid.uuid4()),
        )


def test_tool_upsert_rejects_is_builtin():
    """A non-admin caller must not be able to set ``is_builtin=True``
    on a new tool — that field is admin-only.
    """
    from app.schemas.tool import ToolUpsert

    with pytest.raises(ValidationError):
        ToolUpsert(
            name="rogue",
            description="smuggle",
            parameters={},
            handler_type="internal",
            is_builtin=True,
        )


def test_tool_upsert_rejects_is_admin():
    from app.schemas.tool import ToolUpsert

    with pytest.raises(ValidationError):
        ToolUpsert(
            name="rogue",
            description="smuggle",
            parameters={},
            handler_type="internal",
            is_admin=True,
        )


def test_connector_create_rejects_user_id():
    """H-22 — connector create must refuse to accept a user_id
    (the connector is bound to ``caller`` by the route layer, not
    by a body field).
    """
    from app.schemas.connector import ModelConnectorCreate

    with pytest.raises(ValidationError):
        ModelConnectorCreate(
            name="rogue",
            provider="openai_compat",
            base_url="https://api.example.com",
            default_model="gpt-4o-mini",
            user_id=str(uuid.uuid4()),
        )


def test_connector_create_rejects_id():
    """A new connector cannot pre-assign its own id (cross-tenant write)."""
    from app.schemas.connector import ModelConnectorCreate

    with pytest.raises(ValidationError):
        ModelConnectorCreate(
            name="rogue",
            provider="openai_compat",
            base_url="https://api.example.com",
            default_model="gpt-4o-mini",
            id=str(uuid.uuid4()),
        )


def test_connector_update_rejects_is_admin():
    """C-2 followup — the update path must also refuse ``is_admin``
    so a caller cannot promote an existing connector to admin-shared.
    """
    from app.schemas.connector import ModelConnectorUpdate

    with pytest.raises(ValidationError):
        ModelConnectorUpdate(is_admin=True)

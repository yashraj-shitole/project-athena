"""Unit tests for the PATCH /chat/conversations/{id} rename endpoint.

The other conversation endpoints (create/list/get/delete) are covered
by the integration suite against a live DB. The rename endpoint's
security-critical bits — ownership filter (cross-tenant → 404, not
403), title write, response shape — are cheap to pin at the router
layer with a stub session, mirroring ``test_model_router``'s pattern.
We call ``rename_conversation`` directly so no app/auth/DB wiring is
needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api.chat import rename_conversation
from app.schemas.conversation import ConversationPublic, ConversationRename


# --- Test doubles --------------------------------------------------------

class _Result:
    """Stand-in for the object returned by ``session.execute(...)``."""

    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _StubSession:
    """AsyncSession stand-in: serves one queued row, records the
    commit/refresh side effects the endpoint performs after the write."""

    def __init__(self, row):
        self._row = row
        self.executed = False
        self.committed = False
        self.refreshed = False

    async def execute(self, _stmt):
        self.executed = True
        return _Result(self._row)

    async def commit(self):
        self.committed = True

    async def refresh(self, _conv):
        self.refreshed = True


class _FakeConv:
    """Minimum shape of a ``Conversation`` row the endpoint touches:
    the id/title/user_id it filters + writes on, the timestamps it
    returns, and the ``messages`` relationship it counts."""

    def __init__(self, *, cid, user_id, title, messages=None):
        self.id = cid
        self.user_id = user_id
        self.title = title
        self.created_at = datetime(2024, 1, 1)
        self.updated_at = datetime(2024, 1, 1)
        self.messages = messages or []


# --- Tests ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_rename_conversation_success_writes_and_returns():
    """A rename on the owner's own conversation commits the stripped
    title, refreshes the row, and returns a ConversationPublic whose
    message_count reflects the relationship."""
    user = uuid.uuid4()
    conv = _FakeConv(
        cid=uuid.uuid4(),
        user_id=user,
        title="old title",
        messages=[object(), object()],  # two messages → count 2
    )
    session = _StubSession(conv)

    out = await rename_conversation(
        conv.id,
        ConversationRename(title="  My plan  "),  # schema strips → "My plan"
        user,
        session,
    )

    assert isinstance(out, ConversationPublic)
    assert out.id == conv.id
    assert out.title == "My plan"
    assert out.message_count == 2
    # The endpoint assigns the (already-stripped) payload title onto the
    # row before committing.
    assert conv.title == "My plan"
    assert session.executed
    assert session.committed
    assert session.refreshed


@pytest.mark.asyncio
async def test_rename_conversation_cross_tenant_is_404_not_403():
    """A rename targeting another user's conversation must 404 (not
    403) so the existence of someone else's conversation isn't leaked.
    The stub session returns None — the ownership filter excluded the
    row — and nothing is committed."""
    session = _StubSession(None)

    with pytest.raises(HTTPException) as ei:
        await rename_conversation(
            uuid.uuid4(),
            ConversationRename(title="hijack"),
            uuid.uuid4(),  # a different user
            session,
        )

    assert ei.value.status_code == 404
    assert not session.committed
    assert not session.refreshed


@pytest.mark.asyncio
async def test_rename_conversation_not_found_for_owner():
    """Renaming a nonexistent (or RLS-hidden) conversation for the
    caller also 404s — the not-found path is uniform."""
    session = _StubSession(None)
    user = uuid.uuid4()

    with pytest.raises(HTTPException) as ei:
        await rename_conversation(
            uuid.uuid4(),
            ConversationRename(title="x"),
            user,
            session,
        )

    assert ei.value.status_code == 404
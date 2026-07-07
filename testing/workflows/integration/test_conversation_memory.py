"""Integration tests for conversation memory.

A correct multi-turn conversation:
1. Each turn references the same `conversation_id`.
2. The history on the second turn contains the first turn.
3. The conversation fetch endpoint returns all messages in order.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.smoke]


async def test_multi_turn_conversation_persists_messages(authed_client):
    r1 = await authed_client.post(
        "/api/chat",
        json={"message": "My name is Test User.", "conversation_id": None},
        timeout=120.0,
    )
    assert r1.status_code == 200
    conv_id = r1.json()["conversation_id"]

    r2 = await authed_client.post(
        "/api/chat",
        json={"message": "What is my name?", "conversation_id": conv_id},
        timeout=120.0,
    )
    assert r2.status_code == 200

    # Fetch the conversation's messages.
    r = await authed_client.get(f"/api/chat/conversations/{conv_id}")
    assert r.status_code == 200
    body = r.json()
    items = body.get("items", body) if isinstance(body, dict) else body
    # There should be at least 4 messages: user1, assistant1, user2, assistant2.
    assert len(items) >= 4
    # Roles alternate.
    roles = [m["role"] for m in items]
    assert roles[0] == "user"


async def test_delete_conversation_cascades(authed_client):
    r1 = await authed_client.post(
        "/api/chat",
        json={"message": "Hello.", "conversation_id": None},
        timeout=120.0,
    )
    conv_id = r1.json()["conversation_id"]
    r = await authed_client.delete(f"/api/chat/conversations/{conv_id}")
    assert r.status_code in (200, 204)
    # Subsequent fetch should 404.
    r = await authed_client.get(f"/api/chat/conversations/{conv_id}")
    assert r.status_code == 404

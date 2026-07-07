"""Smoke tests for the chat surface.

Just the non-streaming chat endpoint at the smoke level. The
integration suite exercises streaming + tool calling + retrieval.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.smoke


async def test_chat_non_streaming_returns_envelope(authed_client):
    r = await authed_client.post(
        "/api/chat",
        json={"message": "Say hello.", "conversation_id": None},
        timeout=120.0,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "conversation_id" in data
    assert "message" in data
    msg = data["message"]
    assert msg["role"] == "assistant"
    assert isinstance(msg["content"], str)
    assert len(msg["content"]) > 0


async def test_chat_persists_conversation(authed_client):
    r1 = await authed_client.post(
        "/api/chat",
        json={"message": "Hi.", "conversation_id": None},
        timeout=120.0,
    )
    assert r1.status_code == 200
    conv_id = r1.json()["conversation_id"]
    r2 = await authed_client.post(
        "/api/chat",
        json={"message": "Continue.", "conversation_id": conv_id},
        timeout=120.0,
    )
    assert r2.status_code == 200
    assert r2.json()["conversation_id"] == conv_id


async def test_chat_creates_then_lists_conversation(authed_client):
    r = await authed_client.post(
        "/api/chat",
        json={"message": "Test message", "conversation_id": None},
        timeout=120.0,
    )
    assert r.status_code == 200
    conv_id = r.json()["conversation_id"]
    r = await authed_client.get("/api/chat/conversations")
    assert r.status_code == 200
    body = r.json()
    items = body.get("items", body) if isinstance(body, dict) else body
    assert any(c["id"] == conv_id for c in items)

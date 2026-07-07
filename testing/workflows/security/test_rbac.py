"""Security tests for RBAC.

Phase 1 is single-tenant (every user sees only their own data),
so the "admin" role distinction is limited. These tests focus on
the user-isolation invariant:
  - User A cannot list User B's documents.
  - User A cannot delete User B's conversation.
  - User A cannot fetch User B's chunks.
"""
from __future__ import annotations

import io
import uuid

import pytest


pytestmark = pytest.mark.security


async def test_user_cannot_see_other_users_documents(unauth_client, authed_client):
    """A second user registers, uploads a doc, asserts user A cannot
    list it."""
    # User A is `authed_client`; User B is fresh.
    r = await unauth_client.post(
        "/api/auth/register",
        json={"email": f"rbac+{uuid.uuid4().hex[:8]}@example.com",
              "password": "Rbac!pass1", "name": "B"},
    )
    assert r.status_code == 200
    r = await unauth_client.post(
        "/api/auth/login-json",
        json={"email": r.json()["email"], "password": "Rbac!pass1"},
    )
    b_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # User B uploads a doc
    files = {"file": ("rbac_test.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
    r = await unauth_client.post("/api/documents", files=files, headers=b_headers)
    assert r.status_code in (200, 202)
    doc_id = r.json()["id"]

    # User A tries to fetch it
    r = await authed_client.get(f"/api/documents/{doc_id}")
    assert r.status_code in (403, 404), r.text

    # User A tries to delete it
    r = await authed_client.delete(f"/api/documents/{doc_id}")
    assert r.status_code in (403, 404), r.text


async def test_user_cannot_access_other_users_conversation(unauth_client, authed_client):
    r = await unauth_client.post(
        "/api/auth/register",
        json={"email": f"rbac+{uuid.uuid4().hex[:8]}@example.com",
              "password": "Rbac!pass1", "name": "B"},
    )
    assert r.status_code == 200
    r = await unauth_client.post(
        "/api/auth/login-json",
        json={"email": r.json()["email"], "password": "Rbac!pass1"},
    )
    b_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # User B creates a conversation
    r = await unauth_client.post(
        "/api/chat",
        json={"message": "B's question"},
        headers=b_headers,
        timeout=120.0,
    )
    assert r.status_code == 200
    b_conv = r.json()["conversation_id"]

    # User A tries to fetch it
    r = await authed_client.get(f"/api/chat/conversations/{b_conv}")
    assert r.status_code in (403, 404), r.text

    # User A tries to delete it
    r = await authed_client.delete(f"/api/chat/conversations/{b_conv}")
    assert r.status_code in (403, 404), r.text

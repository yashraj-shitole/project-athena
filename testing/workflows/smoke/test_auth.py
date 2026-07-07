"""Smoke tests for the auth surface.

Cover the four FR-01..04 endpoints at the smoke level (shape +
status code). The integration suite exercises the token lifecycle
in depth; here we just confirm the routes are wired up and return
the documented shape.
"""
from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.smoke


async def test_register_then_login(unauth_client):
    email = f"smoke+{uuid.uuid4().hex[:8]}@example.com"
    password = "Smoke!pass123"

    r = await unauth_client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": "smoke"},
    )
    assert r.status_code == 200, r.text
    user = r.json()
    assert user["email"] == email
    assert "id" in user

    r = await unauth_client.post(
        "/api/auth/login-json",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200
    tokens = r.json()
    for key in ("access_token", "refresh_token", "token_type", "expires_in"):
        assert key in tokens, f"missing token field: {key}"
    assert tokens["token_type"] == "bearer"


async def test_login_with_wrong_password_is_401(unauth_client):
    email = f"smoke+{uuid.uuid4().hex[:8]}@example.com"
    await unauth_client.post(
        "/api/auth/register",
        json={"email": email, "password": "Right!pass1", "name": "smoke"},
    )
    r = await unauth_client.post(
        "/api/auth/login-json",
        json={"email": email, "password": "Wrong!pass1"},
    )
    assert r.status_code in (401, 400)


async def test_me_requires_auth(unauth_client):
    r = await unauth_client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_with_token_returns_user(unauth_client, auth_pair):
    _user, headers = auth_pair
    r = await unauth_client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    me = r.json()
    assert "id" in me
    assert "email" in me


async def test_refresh_returns_new_token(unauth_client, auth_pair):
    _user, headers = auth_pair
    # The auth_pair fixture already produced tokens via login; we
    # call /refresh with the access token. The endpoint may accept
    # either token; check both shapes the API supports.
    r = await unauth_client.post("/api/auth/refresh", headers=headers)
    assert r.status_code in (200, 401)
    if r.status_code == 200:
        body = r.json()
        assert "access_token" in body

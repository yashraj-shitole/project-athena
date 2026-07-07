"""Security tests for CSRF.

The API is JWT-based (Authorization header), not cookie-based, so
classic CSRF doesn't apply. We assert that:
  - POSTing to a protected endpoint without a token is rejected.
  - The response to an unauthenticated POST does not set a session
    cookie.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.security


async def test_unauthenticated_post_is_401(unauth_client):
    r = await unauth_client.post(
        "/api/chat",
        json={"message": "Hi."},
    )
    assert r.status_code == 401


async def test_unauthenticated_post_does_not_set_session_cookie(unauth_client):
    r = await unauth_client.post(
        "/api/chat",
        json={"message": "Hi."},
    )
    set_cookie = r.headers.get("set-cookie", "")
    # No session/auth cookie should be set on a 401.
    assert "session" not in set_cookie.lower()
    assert "auth" not in set_cookie.lower()


async def test_get_requests_do_not_require_csrf_token(unauth_client):
    """GET /health and /model must work without a token AND without
    a CSRF token (the API doesn't use CSRF tokens)."""
    for path in ("/health", "/model", "/metrics", "/openapi.json"):
        r = await unauth_client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"

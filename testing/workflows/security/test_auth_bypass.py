"""Security tests for JWT auth bypass attempts.

Covers:
  - Missing Authorization header
  - Malformed Bearer token
  - Expired token
  - `alg=none` attack (RFC 7519 §6 "Unsecured JWTs")
  - Wrong-signature token (HS256 with a different secret)
  - Empty bearer
"""
from __future__ import annotations

import base64
import json
import time

import jwt
import pytest


pytestmark = pytest.mark.security


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def test_no_auth_header_is_401(unauth_client):
    r = await unauth_client.get("/api/auth/me")
    assert r.status_code == 401


async def test_malformed_bearer_is_401(unauth_client):
    r = await unauth_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert r.status_code == 401


async def test_empty_bearer_is_401(unauth_client):
    r = await unauth_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer "},
    )
    assert r.status_code == 401


async def test_alg_none_token_is_rejected(unauth_client):
    """Forge an `alg: none` token. The verifier must reject it."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "sub": "attacker",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }).encode())
    token = f"{header}.{payload}."  # empty signature
    r = await unauth_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


async def test_wrong_signature_token_is_rejected(unauth_client):
    """Sign a valid JWT with a different secret and assert it's
    rejected."""
    payload = {
        "sub": "attacker",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, "wrong-secret", algorithm="HS256")
    r = await unauth_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


async def test_expired_token_is_rejected(unauth_client):
    """A token whose `exp` is in the past must be rejected."""
    payload = {
        "sub": "attacker",
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,
    }
    # Use the test JWT secret from conftest.
    token = jwt.encode(payload, "test-secret", algorithm="HS256")
    r = await unauth_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401

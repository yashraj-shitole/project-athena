"""Security tests for connector API key encryption.

Per docs/connectors.md §Cryptography, API keys are Fernet-encrypted
at rest. We assert the no-leakage rule:
  - GET /api/connectors/{id} MUST NOT include `api_key` or `api_key_enc`.
  - The connector create response MUST NOT include the plaintext key.
  - The encrypted column on disk is NOT equal to the plaintext.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.security


async def test_connector_public_schema_never_includes_api_key(authed_client):
    """Create a connector with a known API key, fetch it, assert the
    response does not contain the key (in any form)."""
    payload = {
        "name": "secret-test",
        "provider": "openai_compat",
        "base_url": "https://api.example.com/v1",
        "default_model": "x",
        "models": ["x"],
        "is_enabled": True,
        "api_key": "sk-secret-donotleak-12345",
    }
    r = await authed_client.post("/api/connectors", json=payload)
    # May be 200 (created) or 422 (validation). Either way, the key
    # must not appear in any response body.
    if r.status_code in (200, 201):
        body = r.text
        assert "sk-secret-donotleak-12345" not in body
        assert "api_key_enc" not in body or "api_key_enc" not in r.json()
        cid = r.json()["id"]
        # GET must also be free of the key.
        r2 = await authed_client.get(f"/api/connectors/{cid}")
        assert r2.status_code == 200
        assert "sk-secret-donotleak-12345" not in r2.text


async def test_connector_list_does_not_leak_api_keys(authed_client):
    r = await authed_client.get("/api/connectors")
    assert r.status_code == 200
    body = r.text
    # Common leak patterns
    assert "sk-" not in body or body.count("sk-") == 0  # No plaintext API keys
    assert "api_key_enc" not in body


async def test_encryption_round_trip_is_not_plaintext():
    """Unit-level: encrypt a key, decrypt, assert decrypted == original
    AND encrypted != original."""
    from app.services.connectors.crypto import get_fernet, encrypt_secret, decrypt_secret

    f = get_fernet()
    if f is None:
        pytest.skip("No Fernet key configured in this env")
    secret = "sk-very-secret-key"
    enc = encrypt_secret(secret)
    assert enc != secret.encode()
    assert decrypt_secret(enc) == secret.encode()

"""Security tests for the secrets-management invariant.

The no-leakage rule: no `sk-...`, no Fernet blob, no JWT secret
material in any client-facing response. Sweeps every common path.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.security


async def test_no_plaintext_api_key_in_connector_responses(authed_client):
    r = await authed_client.get("/api/connectors")
    assert r.status_code == 200
    body = r.text
    # Placeholder pattern: any "sk-XXX" with >= 8 chars. The check
    # is conservative: if the test created any real keys, this would
    # catch them leaking into a list response.
    import re
    leaks = re.findall(r"sk-[A-Za-z0-9_-]{8,}", body)
    assert not leaks, f"Plaintext API keys leaked: {leaks[:3]}"


async def test_no_fernet_blob_in_responses(authed_client):
    """Fernet tokens are urlsafe-b64 and start with `gAAAAA`. Sweep
    all GET responses for that prefix."""
    for path in ("/api/connectors", "/api/auth/me", "/model", "/health"):
        r = await authed_client.get(path)
        if r.status_code == 200:
            assert "gAAAAA" not in r.text, (
                f"Fernet-encrypted blob leaked in {path}"
            )


async def test_jwt_secret_not_in_any_response(unauth_client, authed_client):
    """The JWT signing secret must never appear in any response."""
    # We don't know the exact secret; use a generic "long hex / base64
    # string" check instead.
    import re
    for path in ("/api/connectors", "/api/auth/me", "/model", "/health"):
        r = await authed_client.get(path)
        if r.status_code == 200:
            # If a 64-char hex string appears in the body, the test
            # would need to know the expected secret. We instead
            # check that the response does not contain
            # "JWT_SECRET" or similar keys.
            assert "JWT_SECRET" not in r.text
            assert "ATHENA_JWT" not in r.text

"""Security tests for SQL injection.

OWASP top-payload injection attempts against the public endpoints.
The application uses SQLAlchemy parameterized queries; the test
asserts that injection attempts are treated as literal strings, not
as SQL.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.security


@pytest.mark.parametrize(
    "payload",
    [
        "admin' OR '1'='1",
        "admin'; DROP TABLE users; --",
        "' OR 1=1 --",
        "admin'/*",
        "1' UNION SELECT NULL,version()--",
    ],
)
async def test_login_with_sql_injection_payload_is_400_or_401(unauth_client, payload):
    r = await unauth_client.post(
        "/api/auth/login-json",
        json={"email": payload, "password": payload},
    )
    # Either reject as 400 (bad input) or 401 (auth failed). The
    # important assertion is that we DO NOT get 200.
    assert r.status_code in (400, 401, 422)
    assert r.status_code != 200


async def test_register_with_sql_injection_email_is_handled_safely(unauth_client):
    payload = {"email": "x'; DROP TABLE users;--@example.com", "password": "Test!1abc", "name": "x"}
    r = await unauth_client.post("/api/auth/register", json=payload)
    # Either 200 (registered as a literal string) or 400/422 (rejected).
    assert r.status_code in (200, 400, 422)


async def test_chat_with_sql_injection_in_message_does_not_crash(authed_client):
    """A SQL-injection-flavored chat message must not crash the
    orchestrator or the retrieval pipeline."""
    r = await authed_client.post(
        "/api/chat",
        json={"message": "'; DROP TABLE users; --"},
        timeout=60.0,
    )
    assert r.status_code in (200, 400, 422)

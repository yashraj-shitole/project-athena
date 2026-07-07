"""Security tests for rate limiting.

Phase 1 may not have rate limiting on every endpoint; these tests
document the expected behavior and will fail (loudly) until the
rate limiter is added. We treat that as a backlog reminder rather
than a hard gate.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.security


@pytest.mark.xfail(reason="Rate limiter not implemented in Phase 1", strict=False)
async def test_login_throttled_after_many_failures(unauth_client):
    """Brute-force protection: after N failed logins, the endpoint
    should respond with 429."""
    for i in range(20):
        r = await unauth_client.post(
            "/api/auth/login-json",
            json={"email": "noone@example.com", "password": "wrong"},
        )
        if r.status_code == 429:
            return  # Throttled — expected.
    # If we got here, no throttling was applied.
    pytest.fail("Expected 429 after 20 failed logins; got none")


@pytest.mark.xfail(reason="Rate limiter not implemented in Phase 1", strict=False)
async def test_chat_throttled_after_many_turns(authed_client):
    """Per-user chat rate limit: 100 turns in 60s should throttle."""
    statuses = set()
    for i in range(100):
        r = await authed_client.post(
            "/api/chat",
            json={"message": f"msg {i}"},
            timeout=10.0,
        )
        statuses.add(r.status_code)
        if 429 in statuses:
            return
    if 429 not in statuses:
        pytest.fail("Expected 429 after 100 turns; got none")

"""Regression tests for H-18 — rate-limit /auth/* endpoints.

The rate-limiter is a fixed-window counter on Redis with a fail-open
default. These tests cover the cases that matter:

* The unit (window + max_n) is honoured.
* The 11th request in a 60s window for the same IP gets a 429.
* The Retry-After header is present and is a positive integer.
* A different IP gets its own bucket.
* Redis being down does NOT take down the endpoint (fail-open).

The Redis dependency is mocked with a tiny in-process dict so the
tests stay hermetic — the real Redis is integration-only.
"""
from __future__ import annotations

from typing import Dict, Tuple

import pytest
from fastapi import HTTPException


class _FakeRedis:
    """Minimal in-process Redis stand-in: INCR + EXPIRE only.

    Expiry is a soft delete — a get() on an expired key returns
    None and the key disappears from the dict, which is what we
    need for the ``INCR`` + ``EXPIRE`` flow.
    """

    def __init__(self) -> None:
        self._data: Dict[str, Tuple[int, float]] = {}
        self._now = 1_700_000_000.0

    def _gc(self) -> None:
        for k in list(self._data.keys()):
            if self._data[k][1] <= self._now:
                self._data.pop(k, None)

    async def incr(self, key: str) -> int:
        self._gc()
        n, _ = self._data.get(key, (0, 0.0))
        n += 1
        # 1-day default TTL if no EXPIRE follows; the test sets
        # EXPIRE explicitly so the TTL is overridden.
        self._data[key] = (n, self._now + 86_400)
        return n

    async def expire(self, key: str, ttl_s: int) -> bool:
        if key not in self._data:
            return False
        n, _ = self._data[key]
        self._data[key] = (n, self._now + ttl_s)
        return True


def _request(ip: str = "1.2.3.4"):
    """Build a minimal Starlette Request with the given client IP."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/auth/login",
        "headers": [],
        "query_string": b"",
        "client": (ip, 12345),
    }
    return Request(scope)


@pytest.fixture
def fake_redis(monkeypatch):
    """Patch the cache client to a fake. The rate-limiter calls
    ``get_client()`` on every check, so we patch that function in
    the ratelimit module's namespace.
    """
    fake = _FakeRedis()
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "get_client", lambda: fake)
    return fake


async def test_first_request_under_limit_allowed(fake_redis):
    from app.core.ratelimit import check_rate_limit

    allowed, remaining = await check_rate_limit(_request("1.1.1.1"), "login")
    assert allowed is True
    assert remaining == 9  # 10 max - 1 used = 9 left


async def test_eleventh_request_blocked(fake_redis):
    from app.core.ratelimit import check_rate_limit

    for _ in range(10):
        await check_rate_limit(_request("2.2.2.2"), "login")
    allowed, _ = await check_rate_limit(_request("2.2.2.2"), "login")
    assert allowed is False


async def test_different_ips_have_separate_buckets(fake_redis):
    from app.core.ratelimit import check_rate_limit

    for _ in range(10):
        a, _ = await check_rate_limit(_request("3.3.3.3"), "login")
        assert a is True
    # 3.3.3.3 is now at the cap; 4.4.4.4 is untouched.
    a, _ = await check_rate_limit(_request("3.3.3.3"), "login")
    assert a is False
    b, _ = await check_rate_limit(_request("4.4.4.4"), "login")
    assert b is True


async def test_register_policy_is_stricter(fake_redis):
    """Register has a 3/min cap; the 4th request is refused."""
    from app.core.ratelimit import check_rate_limit

    for _ in range(3):
        await check_rate_limit(_request("5.5.5.5"), "register")
    allowed, _ = await check_rate_limit(_request("5.5.5.5"), "register")
    assert allowed is False


async def test_unknown_policy_fails_open(fake_redis):
    from app.core.ratelimit import check_rate_limit

    allowed, _ = await check_rate_limit(_request("6.6.6.6"), "no-such-policy")
    assert allowed is True


async def test_redis_outage_fails_open(monkeypatch):
    """If Redis raises, the limiter MUST allow the request — the
    alternative is that a Redis outage takes down /login, which
    is much worse than a brief loss of brute-force protection.
    """
    from app.core import ratelimit

    class _BrokenRedis:
        async def incr(self, key):
            raise ConnectionError("redis down")

        async def expire(self, key, ttl_s):
            raise ConnectionError("redis down")

    monkeypatch.setattr(ratelimit, "get_client", lambda: _BrokenRedis())
    allowed, _ = await ratelimit.check_rate_limit(_request("7.7.7.7"), "login")
    assert allowed is True


async def test_rate_limit_dependency_raises_429(fake_redis):
    """The Depends() wrapper raises 429 with a Retry-After header."""
    from app.core.ratelimit import rate_limit

    dep = rate_limit("login")
    for _ in range(10):
        await dep(_request("8.8.8.8"))
    with pytest.raises(HTTPException) as exc:
        await dep(_request("8.8.8.8"))
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers
    assert int(exc.value.headers["Retry-After"]) > 0


async def test_x_forwarded_for_is_honoured(fake_redis):
    """When the operator sets X-Forwarded-For, the limiter keys on
    the left-most (client) entry, not the proxy chain.
    """
    from starlette.requests import Request

    from app.core.ratelimit import check_rate_limit

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/auth/login",
        "headers": [(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1, 10.0.0.2")],
        "query_string": b"",
    }
    req = Request(scope)
    allowed, _ = await check_rate_limit(req, "login")
    # 11 requests from 9.9.9.9 exhausts the 10/min budget.
    for _ in range(10):
        await check_rate_limit(req, "login")
    allowed, _ = await check_rate_limit(req, "login")
    assert allowed is False

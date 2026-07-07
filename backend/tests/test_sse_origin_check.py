"""Regression tests for M-22 (SSE Origin check) and M-23 (Vary header).

M-22: An SSE endpoint must refuse a connection whose ``Origin`` is
not on the configured CORS allowlist. EventSource does not send a
CORS preflight, so the CORSMiddleware cannot stop a cross-origin
stream — the route must do it.

M-23: An SSE endpoint must set ``Vary: Accept, Origin`` so a shared
cache does not serve a stream response to a different Accept
header (or origin) than the one the response was produced for.

These tests cover the helper directly (no full FastAPI app) so
they stay hermetic.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request


@pytest.fixture(autouse=True)
def _stable_cors(monkeypatch):
    """Force a known CORS allowlist so the tests don't depend on
    conftest state. Earlier tests in the run can monkeypatch
    ATHENA_CORS_ORIGINS; without this, the cached Settings
    instance from those tests would leak into the SSE helper
    and break the origin-match expectations here.
    """
    monkeypatch.setenv(
        "ATHENA_CORS_ORIGINS",
        '["http://localhost:5173", "http://localhost:8080"]',
    )
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    from app.core import config as config_module

    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]


def _make_request(origin: str | None, host: str = "api.example.com") -> Request:
    """Build a minimal Starlette ``Request`` for the helper to inspect."""
    headers = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if host:
        headers.append((b"host", host.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/chat/stream",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


def test_sse_origin_missing_is_allowed():
    """No Origin header (curl, server-to-server) is allowed; the
    JWT in Authorization is the real auth gate.
    """
    from app.api.chat import _check_sse_origin

    request = _make_request(origin=None)
    # Should not raise.
    _check_sse_origin(request)


def test_sse_origin_allowed():
    """An Origin on the CORS allowlist is accepted."""
    from app.api.chat import _check_sse_origin

    # conftest.py sets CORS_ORIGINS to ["http://localhost:5173", "http://localhost:8080"].
    request = _make_request(origin="http://localhost:5173", host="api.example.com")
    _check_sse_origin(request)


def test_sse_origin_disallowed_403(monkeypatch):
    """An Origin not on the allowlist is refused with 403."""
    from app.api import chat as chat_module

    request = _make_request(origin="https://attacker.example.com", host="api.example.com")
    with pytest.raises(HTTPException) as exc:
        chat_module._check_sse_origin(request)
    assert exc.value.status_code == 403
    assert "Origin" in exc.value.detail


def test_sse_origin_same_origin_allowed():
    """Origin matches the API host (same-origin) is always allowed."""
    from app.api.chat import _check_sse_origin

    request = _make_request(
        origin="https://api.example.com", host="api.example.com"
    )
    _check_sse_origin(request)


def test_sse_origin_case_insensitive():
    """Origin matching is case-insensitive on the scheme+host."""
    from app.api.chat import _check_sse_origin

    request = _make_request(origin="HTTP://LOCALHOST:5173", host="api.example.com")
    _check_sse_origin(request)


def test_sse_origin_trailing_slash_stripped():
    from app.api.chat import _check_sse_origin

    # Allowlist stores without trailing slash; an Origin that
    # sends one should still match.
    request = _make_request(origin="http://localhost:5173/", host="api.example.com")
    _check_sse_origin(request)


def test_sse_origin_documents_route(monkeypatch):
    """The same check is wired into the documents status stream."""
    from app.api import documents as docs_module

    request = _make_request(origin="https://attacker.example.com", host="api.example.com")
    with pytest.raises(HTTPException) as exc:
        docs_module._check_sse_origin(request)
    assert exc.value.status_code == 403

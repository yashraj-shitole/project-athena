"""Regression tests for M-28 — security headers middleware.

The middleware adds the standard hardening headers (HSTS, nosniff,
X-Frame-Options, etc.) to every response. We use FastAPI's TestClient
to drive a full request and assert the headers are present.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security_headers import SecurityHeadersMiddleware


@pytest.fixture
def client(monkeypatch):
    """Build a minimal app that just echoes the request — enough to
    assert the middleware adds headers to *every* response.
    """
    from app.core import config as config_module

    # Avoid leaking the cached Settings from a previous test that
    # may have set the JWT secret to an invalid value.
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/")
    async def root():
        return {"ok": True}

    return TestClient(app)


def test_basic_headers_always_set(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in r.headers
    assert r.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert r.headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_hsts_not_set_in_dev_over_http(client):
    """HSTS MUST NOT be sent in dev over plain HTTP. Pinning HSTS
    on a non-TLS endpoint would brick the dev experience.
    """
    r = client.get("/")
    assert "Strict-Transport-Security" not in r.headers


def test_hsts_set_when_x_forwarded_proto_https(client):
    """HSTS is sent when the request arrived over TLS (proxied)."""
    r = client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" in r.headers
    assert "max-age" in r.headers["Strict-Transport-Security"]


def test_csp_includes_frame_ancestors_none(client):
    """CSP ``frame-ancestors 'none'`` defeats iframe embedding."""
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_csp_default_src_self(client):
    """CSP ``default-src 'self'`` blocks third-party script loading."""
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_csp_prod_stricter_than_dev(monkeypatch, client):
    """In prod the CSP forbids inline scripts; in dev it allows them
    (Vite HMR uses eval). We exercise the prod branch by toggling
    the environment to ``prod``.
    """
    from app.core import config as config_module

    monkeypatch.setenv("ATHENA_ENVIRONMENT", "prod")
    monkeypatch.setenv("ATHENA_CORS_ORIGINS", '["https://app.example.com"]')
    monkeypatch.setenv(
        "ATHENA_JWT_SECRET",
        "abcdef0123456789ABCDEF0123456789XyZ",  # 35 bytes
    )
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]

    r = client.get("/")
    csp = r.headers["Content-Security-Policy"]
    # prod CSP must NOT contain 'unsafe-inline' or 'unsafe-eval'
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp

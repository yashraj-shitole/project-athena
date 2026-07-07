"""Security tests for input validation.

Asserts that the Pydantic schemas reject malformed input with 422
(validation error) or 400 (bad request), and never crash.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.security


async def test_register_with_invalid_email_is_422(unauth_client):
    r = await unauth_client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "Test!1abc", "name": "x"},
    )
    assert r.status_code in (400, 422)


async def test_register_with_short_password_is_422(unauth_client):
    r = await unauth_client.post(
        "/api/auth/register",
        json={"email": "x@example.com", "password": "short", "name": "x"},
    )
    assert r.status_code in (400, 422)


async def test_create_connector_with_invalid_provider_is_422(authed_client):
    r = await authed_client.post(
        "/api/connectors",
        json={
            "name": "x",
            "provider": "NOT_A_REAL_PROVIDER",
            "base_url": "https://x.example.com",
            "default_model": "x",
            "models": ["x"],
        },
    )
    assert r.status_code in (400, 422)


async def test_create_connector_with_invalid_base_url_is_422(authed_client):
    r = await authed_client.post(
        "/api/connectors",
        json={
            "name": "x",
            "provider": "openai_compat",
            "base_url": "not-a-url",
            "default_model": "x",
            "models": ["x"],
        },
    )
    assert r.status_code in (400, 422)

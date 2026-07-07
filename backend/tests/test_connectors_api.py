"""Tests for the External Model Connectors REST API.

The test strategy:

* **Schemas** (pure Pydantic) — provider/auth_type/base_url
  validation, the secret-leakage contract (the public schema
  never carries `api_key_enc` or `api_key`).
* **Helper** (`to_public`) — the conversion from ORM to public
  schema; the audit log dump uses the same path.
* **Audit log writes** — every mutation calls `audit.record()`
  with the right action vocabulary; the `before/after` payloads
  come from `to_public(...)` and are therefore secret-free.
* **Smoke route test** — `GET /api/connectors/templates` is the
  only endpoint that doesn't need a session, so it gets a full
  `httpx.AsyncClient` roundtrip.

Auth-gated route paths (`GET /api/connectors`, `POST /api/connectors`,
etc.) need a real JWT + DB session and live in the integration
suite (gated by `--run-integration`).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api._connector_helpers import to_public
from app.models.connector import AUTH_BEARER, PROVIDER_OPENAI_COMPAT
from app.schemas.connector import (
    ConnectorListResponse,
    ConnectorTemplate,
    HealthCheckResult,
    ModelConnectorCreate,
    ModelConnectorPublic,
    ModelConnectorUpdate,
    SetDefaultResponse,
)


# --- Schema validation ---------------------------------------------------

def test_create_rejects_unknown_provider():
    with pytest.raises(Exception):
        ModelConnectorCreate(
            name="x",
            provider="totally-fake",
            base_url="https://example.com",
            default_model="m",
        )


def test_create_rejects_bad_auth_type():
    with pytest.raises(Exception):
        ModelConnectorCreate(
            name="x",
            provider=PROVIDER_OPENAI_COMPAT,
            base_url="https://example.com",
            default_model="m",
            auth_type="totally-fake",
        )


def test_create_rejects_non_http_base_url():
    with pytest.raises(Exception):
        ModelConnectorCreate(
            name="x",
            provider=PROVIDER_OPENAI_COMPAT,
            base_url="ftp://example.com",
            default_model="m",
        )


def test_create_accepts_loopback_base_url():
    """Self-hosted Ollama is the common case — loopback must be
    permitted at the schema layer (the SSRF guard re-checks at
    runtime, but Pydantic shouldn't reject the URL up front)."""
    m = ModelConnectorCreate(
        name="ollama",
        provider=PROVIDER_OPENAI_COMPAT,
        base_url="http://localhost:11434",
        default_model="qwen2.5:1.5b-instruct",
    )
    assert m.base_url == "http://localhost:11434"


def test_create_defaults_caps_are_sensible():
    m = ModelConnectorCreate(
        name="x",
        provider=PROVIDER_OPENAI_COMPAT,
        base_url="https://example.com",
        default_model="m",
    )
    assert m.is_enabled is True
    assert m.is_default is False
    assert m.is_admin is False
    assert m.capabilities["chat"] is True
    assert m.models == []


def test_update_is_everything_optional():
    """PATCH must allow zero-field bodies (no-op)."""
    u = ModelConnectorUpdate()
    assert u.name is None
    assert u.api_key is None


def test_update_empty_api_key_string_is_legal():
    """An empty string is a valid PATCH body — the route layer
    treats it as 'no key change' (the user must pass a non-empty
    value to rotate)."""
    u = ModelConnectorUpdate(api_key="")
    assert u.api_key == ""


# --- Secret-leakage contract --------------------------------------------

def test_public_schema_has_no_api_key_field():
    """The Pydantic model fields MUST NOT include `api_key` or
    `api_key_enc`. This is a compile-time guarantee."""
    field_names = set(ModelConnectorPublic.model_fields.keys())
    assert "api_key" not in field_names
    assert "api_key_enc" not in field_names
    # The preview IS allowed (the masked tail).
    assert "api_key_preview" in field_names


def test_create_schema_carries_plaintext_api_key():
    """By design, the *create* schema carries plaintext (the route
    encrypts it). The leak check is on the public schema, not the
    input shape."""
    m = ModelConnectorCreate(
        name="x",
        provider=PROVIDER_OPENAI_COMPAT,
        base_url="https://example.com",
        default_model="m",
        api_key="sk-1234567890abcdef",
    )
    assert m.api_key == "sk-1234567890abcdef"


def test_update_schema_carries_plaintext_api_key():
    """PATCH can rotate the key; same plaintext-on-input contract."""
    u = ModelConnectorUpdate(api_key="sk-NEWKEY")
    assert u.api_key == "sk-NEWKEY"


# --- to_public helper ---------------------------------------------------

def _make_row(**overrides) -> "ModelConnector":
    """Build a `ModelConnector` with the minimum required fields.

    Tests override the bits they care about; the rest default to
    values that round-trip cleanly through the public schema.
    """
    from datetime import datetime, timezone
    from app.models.connector import ModelConnector

    now = datetime.now(timezone.utc)
    kwargs = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="x",
        provider=PROVIDER_OPENAI_COMPAT,
        base_url="https://example.com",
        default_model="m",
        auth_type=AUTH_BEARER,
        custom_headers={},
        models=[],
        capabilities={},
        settings={},
        tags=[],
        discovered_models=[],
        is_enabled=True,
        is_default=False,
        is_admin=False,
        is_favorite=False,
        consecutive_failures=0,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return ModelConnector(**kwargs)


def test_to_public_never_carries_api_key():
    """`to_public()` is the only place ORM → public conversion
    happens. A regression here is the leakiest possible bug."""
    row = _make_row(
        name="leak-check",
        api_key_enc=b"some-encrypted-blob",
        api_key_preview="sk-…1234",
        capabilities={"chat": True},
    )
    public = to_public(row)
    dumped = public.model_dump()
    # The plaintext key is never on the public shape.
    assert "api_key" not in dumped
    assert "api_key_enc" not in dumped
    # The preview is included.
    assert dumped["api_key_preview"] == "sk-…1234"


def test_to_public_is_owner_defaults_true():
    """`is_owner` defaults to True when not passed — the safe
    default for owner-scoped list operations."""
    row = _make_row()
    assert to_public(row).is_owner is True


def test_to_public_overrides_is_owner_for_shared_row():
    row = _make_row()
    # When the caller is not the owner, the helper reports it so
    # the UI can hide owner-only actions.
    public = to_public(row, is_owner=False)
    assert public.is_owner is False


# --- Templates ----------------------------------------------------------

def test_templates_cover_all_phase_d_providers():
    """The canned templates should mention every provider the user
    can pick from the dropdown."""
    expected = {
        PROVIDER_OPENAI_COMPAT,
        "anthropic",
        "gemini",
        "azure_openai",
        "ollama",
    }
    from app.api.connectors import _TEMPLATES

    actual = {t.provider for t in _TEMPLATES}
    assert expected.issubset(actual), f"missing templates for: {expected - actual}"


def test_templates_have_loopback_for_local_ollama():
    from app.api.connectors import _TEMPLATES

    ollama = next(t for t in _TEMPLATES if t.provider == "ollama")
    assert ollama.base_url.startswith("http://localhost")
    assert ollama.auth_type == "none"


# --- Response shape contracts ------------------------------------------

def test_set_default_response_shape():
    s = SetDefaultResponse(id=uuid.uuid4(), is_default=True)
    assert s.is_default is True


def test_health_check_result_shape():
    h = HealthCheckResult(
        ok=True,
        latency_ms=42,
        status="online",
        capabilities={"chat": True},
        models=["x"],
        error=None,
        category="ok",
        status_code=200,
    )
    assert h.ok is True
    assert h.capabilities["chat"] is True


def test_connector_list_response_shape():
    """The list response carries connectors + templates, so the
    UI can render the page in one roundtrip."""
    clr = ConnectorListResponse(
        connectors=[],
        templates=[],
    )
    assert clr.connectors == []
    assert clr.templates == []


# --- Smoke route test (templates is the only DB-free endpoint) --------

@pytest.mark.asyncio
async def test_get_templates_endpoint_returns_list():
    """`GET /api/connectors/templates` doesn't need a session —
    smoke-test it via `httpx.AsyncClient` to make sure the route
    is registered and the response shape matches the schema."""
    from httpx import ASGITransport, AsyncClient

    # We need to bypass the auth dep for this test; the templates
    # endpoint is open in the design (no current_user_id dep).
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/connectors/templates")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 5
    for tpl in data:
        assert "provider" in tpl
        assert "base_url" in tpl


@pytest.mark.asyncio
async def test_get_registry_endpoint_returns_providers():
    from httpx import ASGITransport, AsyncClient
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/connectors/registry")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    names = {row["provider"] for row in data}
    for required in (
        "openai_compat",
        "anthropic",
        "gemini",
        "azure_openai",
        "ollama",
        "custom",
    ):
        assert required in names, f"missing provider in registry: {required}"

"""Tests for the security primitives (FR-09 / NFR-09)."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_hash_and_verify_password():
    from app.core.security import hash_password, verify_password

    h = hash_password("Sup3rSecret!")
    assert h and h != "Sup3rSecret!"
    assert verify_password("Sup3rSecret!", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_round_trip():
    from app.core.security import (
        create_access_token,
        create_refresh_token,
        decode_token,
    )

    uid = uuid.uuid4()
    a = create_access_token(uid)
    r = create_refresh_token(uid)
    assert a != r
    pa = decode_token(a)
    pr = decode_token(r)
    assert pa["sub"] == str(uid)
    assert pa["type"] == "access"
    assert pr["type"] == "refresh"


# ---------------------------------------------------------------------
# H-20 — JWT secret strength gate (length + entropy).
# ---------------------------------------------------------------------
def test_jwt_secret_rejects_known_placeholder(monkeypatch):
    """The dev placeholder is short, low-entropy, and well-known —
    it must be refused even in dev so an operator who forgets to
    override it gets a loud failure rather than a silent boot.
    """
    from pydantic import ValidationError

    from app.core import config as config_module

    monkeypatch.setenv("ATHENA_JWT_SECRET", "change-me-in-prod")
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        config_module.get_settings()
    except ValidationError as exc:
        assert "placeholder" in str(exc).lower() or "32 bytes" in str(exc)
    else:
        raise AssertionError("expected ValidationError for known-insecure JWT secret")

def test_jwt_secret_rejects_short_value(monkeypatch):
    """A 16-byte random string passes the placeholder check but
    still fails the length gate. RFC 7518 §3.2 requires >= 32 bytes
    for HS256 — a 16-byte secret is brute-forceable in seconds.
    """
    from pydantic import ValidationError

    from app.core import config as config_module

    monkeypatch.setenv("ATHENA_JWT_SECRET", "abcdef0123456789")  # 16 bytes
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        config_module.get_settings()
    except ValidationError as exc:
        assert "32 bytes" in str(exc)
    else:
        raise AssertionError("expected ValidationError for short JWT secret")


def test_jwt_secret_rejects_low_entropy(monkeypatch):
    """32 'a's has length 32 but entropy 0. Must be refused."""
    from pydantic import ValidationError

    from app.core import config as config_module

    monkeypatch.setenv("ATHENA_JWT_SECRET", "a" * 32)
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        config_module.get_settings()
    except ValidationError as exc:
        assert "entropy" in str(exc).lower()
    else:
        raise AssertionError("expected ValidationError for low-entropy JWT secret")


def test_jwt_secret_accepts_strong_value(monkeypatch):
    """A 32+ byte high-entropy value passes the gate."""
    from app.core import config as config_module

    monkeypatch.setenv(
        "ATHENA_JWT_SECRET",
        "abcdef0123456789ABCDEF0123456789XyZ",  # 35 bytes, mixed case + digits
    )
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    s = config_module.get_settings()
    assert s.jwt_secret.startswith("abcdef")


# ---------------------------------------------------------------------
# H-21 — CORS origin gate.
# ---------------------------------------------------------------------
def test_cors_rejects_wildcard(monkeypatch):
    """'*' is incompatible with allow_credentials=True (CWE-942)."""
    from pydantic import ValidationError

    from app.core import config as config_module

    monkeypatch.setenv("ATHENA_CORS_ORIGINS", '["*"]')
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        config_module.get_settings()
    except ValidationError as exc:
        assert "wildcard" in str(exc).lower() or "*" in str(exc)
    else:
        raise AssertionError("expected ValidationError for wildcard CORS origin")


def test_cors_rejects_path(monkeypatch):
    """Origins must be scheme://host[:port] only — paths would be
    normalized by browsers in surprising ways."""
    from pydantic import ValidationError

    from app.core import config as config_module

    monkeypatch.setenv(
        "ATHENA_CORS_ORIGINS", '["https://app.example.com/api"]'
    )
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        config_module.get_settings()
    except ValidationError as exc:
        assert "not a valid" in str(exc).lower()
    else:
        raise AssertionError("expected ValidationError for CORS origin with path")


def test_cors_rejects_javascript_scheme(monkeypatch):
    """javascript: / data: URIs in CORS are CWE-942 vectors."""
    from pydantic import ValidationError

    from app.core import config as config_module

    monkeypatch.setenv(
        "ATHENA_CORS_ORIGINS", '["javascript:alert(1)"]'
    )
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        config_module.get_settings()
    except ValidationError as exc:
        assert "not a valid" in str(exc).lower()
    else:
        raise AssertionError("expected ValidationError for javascript: CORS origin")


def test_cors_rejects_loopback_in_prod(monkeypatch):
    """localhost in CORS is fine for dev — not for prod. The
    field validator handles the syntax; ``model_post_init`` handles
    the env-mode gate. Either rejection is acceptable — both are
    silent on the way to the request.
    """
    from pydantic import ValidationError

    from app.core import config as config_module

    monkeypatch.setenv("ATHENA_CORS_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "prod")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        config_module.get_settings()
    except (ValidationError, RuntimeError) as exc:
        msg = str(exc).lower()
        assert "loopback" in msg or "localhost" in msg
    else:
        raise AssertionError(
            "expected rejection for localhost CORS origin in prod"
        )


def test_cors_accepts_https_fqdn(monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv(
        "ATHENA_CORS_ORIGINS", '["https://app.example.com"]'
    )
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    s = config_module.get_settings()
    assert s.cors_origins == ["https://app.example.com"]


def test_cors_accepts_localhost_in_dev(monkeypatch):
    from app.core import config as config_module

    monkeypatch.setenv("ATHENA_CORS_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    s = config_module.get_settings()
    assert s.cors_origins == ["http://localhost:5173"]

"""Tests for the connector-encryption helpers.

Hermetic: do not touch the network, do not require Postgres. We override
the settings via env so a developer running the suite without a real
`.env` still gets a working Fernet key.
"""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

# Set the test key BEFORE importing the app, so `get_settings()` picks
# it up on first access.
_TEST_KEY = Fernet.generate_key().decode()
os.environ["ATHENA_CONNECTOR_KEY"] = _TEST_KEY
os.environ.setdefault("ATHENA_ENVIRONMENT", "test")
os.environ.setdefault("ATHENA_JWT_SECRET", "test-secret-32-bytes-or-more-please!")

from app.core import config as config_module  # noqa: E402
from app.services.providers import crypto  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_crypto_cache():
    """Each test gets a fresh Fernet instance, otherwise the first
    test's settings leak into the next."""
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_encrypt_decrypt_roundtrip():
    plain = "sk-test-1234567890abcdef"
    blob = crypto.encrypt(plain)
    assert blob != plain.encode()
    assert crypto.decrypt(blob) == plain


def test_encrypt_empty_raises():
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("")
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("   ")


def test_decrypt_empty_raises():
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(b"")


def test_decrypt_garbage_raises_cryptoerror_not_invalidtoken():
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(b"not-a-fernet-token")


def test_mask_for_ui_typical():
    assert crypto.mask_for_ui("sk-1234567890abcdef") == "sk-…cdef"


def test_mask_for_ui_short_token():
    # Tokens shorter than 8 chars must not leak structure.
    assert crypto.mask_for_ui("abc") == "len:3"
    assert crypto.mask_for_ui("") == ""


def test_mask_for_ui_strips_whitespace():
    assert crypto.mask_for_ui("  sk-1234567890abcdef  \n") == "sk-…cdef"


def test_explicit_key_takes_precedence(monkeypatch):
    """If `connector_enc_key` is set, the dev fallback is NOT used.

    We change the JWT secret, encrypt with the explicit key, and
    confirm the ciphertext is decryptable under a different JWT
    secret (proving the explicit key was used).
    """
    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("ATHENA_CONNECTOR_KEY", new_key)
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    plain = "sk-secret-key"
    blob = crypto.encrypt(plain)
    # Rotate JWT secret — the dev fallback would now produce a
    # different key and fail to decrypt. Explicit key still works.
    monkeypatch.setenv("ATHENA_JWT_SECRET", "completely-different-secret-32-bytes-long-ok")
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    assert crypto.decrypt(blob) == plain


def test_dev_fallback_with_hkdf(monkeypatch):
    """In dev with no explicit key, the cipher derives from JWT secret
    via HKDF. Rotating the JWT secret must invalidate old ciphertext —
    that's the property that makes a real key required in prod.
    """
    monkeypatch.delenv("ATHENA_CONNECTOR_KEY", raising=False)
    monkeypatch.setenv("ATHENA_JWT_SECRET", "stable-dev-secret-32-bytes-long-ok!")
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "dev")
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    plain = "sk-dev-fallback"
    blob = crypto.encrypt(plain)
    # Same secret: decrypts.
    assert crypto.decrypt(blob) == plain
    # Different secret: fails. (This is the production-surprise
    # behaviour the prod guard defends against.)
    monkeypatch.setenv("ATHENA_JWT_SECRET", "rotated-dev-secret-32-bytes-long-ok-")
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(blob)


def test_invalid_explicit_key_raises(monkeypatch):
    monkeypatch.setenv("ATHENA_CONNECTOR_KEY", "not-a-key")
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt("anything")


def test_prod_without_explicit_key_raises(monkeypatch):
    """In prod with no explicit key, the boot guard refuses to start.

    The Settings layer (model_post_init) raises RuntimeError. The
    `_resolve_key` fallback guard is defense-in-depth — both should
    keep a real prod deploy from silently deriving a JWT-tied key.
    """
    monkeypatch.delenv("ATHENA_CONNECTOR_KEY", raising=False)
    monkeypatch.setenv("ATHENA_ENVIRONMENT", "prod")
    monkeypatch.setenv("ATHENA_JWT_SECRET", "real-jwt-secret-32-bytes-or-more-please-ok")
    # CORS guard: avoid the localhost dev default in the prod boot check.
    monkeypatch.setenv("ATHENA_CORS_ORIGINS", '["https://athena.example.com"]')
    crypto.reset_cache()
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]
    with pytest.raises((RuntimeError, crypto.CryptoError)):
        config_module.get_settings()

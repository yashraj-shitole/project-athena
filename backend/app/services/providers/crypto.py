"""Encryption helpers for connector API keys.

The encrypted blob lives in `model_connectors.api_key_enc` (BYTEA). We
use Fernet (AES-128-CBC + HMAC-SHA256, time-stamped) from
`cryptography` — simpler than raw AES-GCM, and Fernet tokens are
self-describing which makes key rotation straightforward.

Key source, in priority order:
  1. `ATHENA_CONNECTOR_KEY` env var (urlsafe-base64 32-byte). The
     operator is responsible for generating and storing this.
  2. HKDF-SHA256 derivation from `ATHENA_JWT_SECRET` with info string
     `b"connector-enc-v1"`. This is a **dev-only fallback** so a fresh
     checkout works without manual setup; it ties key lifetime to JWT
     rotation (surprising in production). The config layer refuses to
     start in non-dev environments when the fallback is in use.

The plaintext key is never logged. The mask helper returns a UI-safe
preview string; the public Pydantic schema carries only that preview.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Sentinel info string for the dev fallback. Bump this when rotating the
# scheme — the version is encoded in the KDF input, so a single
# generation key is forward-compatible with future KDF variants (we just
# won't be able to *decrypt* blobs produced under the old scheme).
_DEV_KDF_INFO = b"connector-enc-v1"

# Known-insecure JWT secrets that the config layer also rejects — we
# refuse to derive a dev fallback key from them so a misconfigured
# production server cannot accidentally produce a deterministic key.
_INSECURE_FALLBACK_SECRETS = {"change-me-in-prod", "", "secret", "changeme"}


class CryptoError(RuntimeError):
    """Raised when encrypt/decrypt cannot be performed safely."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Build the (singleton) Fernet cipher from the configured key.

    Cached because Fernet construction is not free, and we call it on
    every decrypt. `lru_cache` means a test that mutates settings has
    to call `_fernet.cache_clear()` (tests do this).
    """
    settings = get_settings()
    key = _resolve_key(settings.connector_key, settings.jwt_secret, settings.environment)
    return Fernet(key)


def _resolve_key(
    explicit: str,
    jwt_secret: str,
    environment: str,
) -> bytes:
    """Return a urlsafe-base64 32-byte Fernet key.

    In prod, `explicit` is required. In dev, we fall back to HKDF over
    the JWT secret so a fresh checkout works; this is loud-logged so
    an operator sees the warning.
    """
    if explicit:
        try:
            return _coerce_fernet_key(explicit)
        except ValueError as exc:
            raise CryptoError(
                f"ATHENA_CONNECTOR_KEY is set but invalid: {exc}. "
                "Generate one with `python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\"`."
            ) from exc

    env_lc = (environment or "").lower()
    if env_lc not in {"dev", "development", "test", "local"}:
        # The Settings.model_post_init guard catches this earlier, but
        # defend in depth so a direct caller can't slip past.
        raise CryptoError(
            "ATHENA_CONNECTOR_KEY is required outside dev. Refusing to "
            "derive a fallback key from ATHENA_JWT_SECRET."
        )
    if jwt_secret in _INSECURE_FALLBACK_SECRETS:
        raise CryptoError(
            "Cannot derive a connector-encryption key from an insecure "
            "ATHENA_JWT_SECRET. Set ATHENA_CONNECTOR_KEY or a real JWT "
            "secret."
        )
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_DEV_KDF_INFO,
    ).derive(jwt_secret.encode("utf-8"))
    log.warning(
        "crypto.dev_fallback_key",
        note=(
            "Derived connector-encryption key from ATHENA_JWT_SECRET via "
            "HKDF. Set ATHENA_CONNECTOR_KEY in production — rotating the "
            "JWT secret will also invalidate every stored API key."
        ),
    )
    return base64.urlsafe_b64encode(derived)


def _coerce_fernet_key(raw: str) -> bytes:
    """Accept a base64 or base64url-encoded 32-byte key.

    Fernet requires `urlsafe-base64` 32 bytes; we tolerate the standard
    base64 form too because operators copy-paste from various
    instructions.
    """
    raw = raw.strip()
    # Try urlsafe first (the documented format).
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(raw + "=" * (-len(raw) % 4))
            if len(decoded) == 32:
                return base64.urlsafe_b64encode(decoded)
        except Exception:  # noqa: BLE001
            continue
    raise ValueError("not a 32-byte Fernet key")


def encrypt(plain: str) -> bytes:
    """Encrypt a plaintext string. Returns Fernet ciphertext bytes.

    Empty / whitespace-only input raises — call sites must not call
    `encrypt("")` and rely on it being a no-op; storing an empty key
    is almost always a bug. Whitespace-only would encrypt to a real
    ciphertext that decrypts back to whitespace, masking the mistake.
    """
    if not plain or not plain.strip():
        raise CryptoError("encrypt() refused empty plaintext")
    return _fernet().encrypt(plain.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    """Decrypt a Fernet ciphertext blob. Raises CryptoError on failure.

    Bad ciphertext is mapped to CryptoError (not the underlying
    InvalidToken) so callers can catch one type.
    """
    if not blob:
        raise CryptoError("decrypt() refused empty ciphertext")
    try:
        return _fernet().decrypt(blob).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError(
            "Stored connector key cannot be decrypted — the encryption "
            "key has changed since this row was written. The user must "
            "re-enter the API key."
        ) from exc


def mask_for_ui(plain: str) -> str:
    """Return a UI-safe preview of `plain` (e.g. `sk-…1234`).

    Tokens shorter than 8 chars would leak too much — return a length
    marker instead. Tokens with whitespace (e.g. an accidentally
    pasted header) are stripped of trailing whitespace first.
    """
    if not plain:
        return ""
    s = plain.strip()
    if len(s) < 8:
        return f"len:{len(s)}"
    return f"{s[:3]}…{s[-4:]}"


# Convenience for tests that swap the settings at runtime.
def reset_cache() -> None:
    _fernet.cache_clear()


__all__ = [
    "CryptoError",
    "encrypt",
    "decrypt",
    "mask_for_ui",
    "reset_cache",
]

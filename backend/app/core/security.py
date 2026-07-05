"""Authentication primitives: password hashing + JWT issue/verify."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import bcrypt
import jwt

from app.core.config import get_settings

_settings = get_settings()

# bcrypt rejects passwords longer than 72 bytes; we truncate to stay
# within the limit while still producing a deterministic, well-known
# behaviour (rather than raising at hash time).
_BCRYPT_MAX_BYTES = 72


def _normalize(plain: str) -> bytes:
    raw = plain.encode("utf-8")
    return raw[:_BCRYPT_MAX_BYTES]


# -------- password --------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_normalize(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_normalize(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# -------- JWT --------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(payload: Dict[str, Any], ttl: timedelta) -> str:
    payload = {
        **payload,
        "iat": int(_now().timestamp()),
        "exp": int((_now() + ttl).timestamp()),
    }
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def create_access_token(user_id: uuid.UUID, token_version: int = 0) -> str:
    return _encode(
        {"sub": str(user_id), "type": "access", "ver": int(token_version)},
        timedelta(minutes=_settings.access_token_ttl_min),
    )


def create_refresh_token(user_id: uuid.UUID, token_version: int = 0) -> str:
    return _encode(
        {"sub": str(user_id), "type": "refresh", "ver": int(token_version)},
        timedelta(days=_settings.refresh_token_ttl_days),
    )


def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, _settings.jwt_secret, algorithms=[_settings.jwt_algorithm])

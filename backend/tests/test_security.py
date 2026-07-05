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

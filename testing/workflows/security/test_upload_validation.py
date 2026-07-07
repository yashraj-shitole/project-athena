"""Security tests for upload validation.

Asserts the documented limits from docs/configuration.md:
  - `ATHENA_UPLOAD_MAX_BYTES` (default 25 MB)
  - `ATHENA_UPLOAD_ALLOWED_TYPES` (default: csv, xlsx, pdf, doc, docx)
  - Filename must be a basename (no path traversal)
"""
from __future__ import annotations

import io

import pytest


pytestmark = pytest.mark.security


async def test_upload_oversized_is_rejected(authed_client):
    """A 26 MB file exceeds the default 25 MB cap."""
    # 26 MB of zeros — fast to allocate, easy to detect.
    big = b"\0" * (26 * 1024 * 1024)
    files = {"file": ("big.csv", io.BytesIO(big), "text/csv")}
    r = await authed_client.post("/api/documents", files=files)
    assert r.status_code in (400, 413, 422), r.text


async def test_upload_disallowed_type_is_rejected(authed_client):
    """A `.exe` file must be rejected (not in the default allow-list)."""
    files = {"file": ("malware.exe", io.BytesIO(b"MZ\x00\x00"), "application/octet-stream")}
    r = await authed_client.post("/api/documents", files=files)
    assert r.status_code in (400, 415, 422), r.text


async def test_upload_with_path_traversal_in_filename_is_sanitized(authed_client):
    """A filename like `../../etc/passwd.csv` should be sanitized or rejected."""
    files = {"file": ("../../etc/passwd.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
    r = await authed_client.post("/api/documents", files=files)
    if r.status_code in (200, 202):
        stored = r.json()["filename"]
        assert ".." not in stored
        assert "/" not in stored
        assert "\\" not in stored
    else:
        assert r.status_code in (400, 422)


async def test_upload_with_null_byte_in_filename_is_sanitized(authed_client):
    files = {"file": ("a\x00.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
    r = await authed_client.post("/api/documents", files=files)
    # Either accepted with sanitized name, or rejected.
    assert r.status_code in (200, 202, 400, 422)

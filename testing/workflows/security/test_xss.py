"""Security tests for XSS in the document chunks endpoint.

A document with `<script>` content must be HTML-escaped (or at
least not served with a `text/html` Content-Type) by the chunks
endpoint. The frontend renders chunks as text inside a React
component, so the primary defense is in the client — but the API
should not set `text/html`.
"""
from __future__ import annotations

import io

import pytest


pytestmark = pytest.mark.security


async def test_chunks_endpoint_is_json_not_html(authed_client):
    """The chunks endpoint must return `application/json` (or no
    content-type at all), never `text/html`."""
    csv = b"name,value\nrow,1\n"
    r = await authed_client.post(
        "/api/documents",
        files={"file": ("xss_test.csv", io.BytesIO(csv), "text/csv")},
    )
    assert r.status_code in (200, 202)
    doc_id = r.json()["id"]
    r = await authed_client.get(f"/api/documents/{doc_id}/chunks")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        ct = r.headers.get("content-type", "")
        assert "text/html" not in ct, f"chunks endpoint returned HTML: {ct}"


async def test_filename_with_traversal_is_sanitized(authed_client):
    """A filename with `../` must be sanitized; the document's stored
    filename must not contain a path-traversal segment."""
    files = {"file": ("../../etc/passwd.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
    r = await authed_client.post("/api/documents", files=files)
    # Either rejected (400) or sanitized (200 with a clean name).
    assert r.status_code in (200, 202, 400, 422)
    if r.status_code in (200, 202):
        stored_name = r.json()["filename"]
        assert ".." not in stored_name
        assert "/" not in stored_name
        assert "\\" not in stored_name

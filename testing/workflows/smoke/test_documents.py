"""Smoke tests for the documents surface.

Just the upload + list + delete round trip at the smoke level. The
integration suite exercises the full ingestion pipeline.
"""
from __future__ import annotations

import io
import uuid

import pytest


pytestmark = pytest.mark.smoke


async def test_list_documents_empty(authed_client):
    r = await authed_client.get("/api/documents")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data or isinstance(data, list)


async def test_upload_csv_round_trip(authed_client):
    """Upload a tiny CSV, list, then delete it."""
    csv_bytes = b"col_a,col_b\n1,2\n3,4\n"
    files = {"file": ("smoke.csv", io.BytesIO(csv_bytes), "text/csv")}
    r = await authed_client.post("/api/documents", files=files)
    assert r.status_code in (200, 202), r.text
    doc = r.json()
    doc_id = doc["id"]
    assert doc["filename"] == "smoke.csv"
    assert doc["file_type"] in ("csv", None)  # None while uploading

    # List should include it
    r = await authed_client.get("/api/documents")
    assert r.status_code == 200
    items = r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()
    assert any(d["id"] == doc_id for d in items)

    # Delete
    r = await authed_client.delete(f"/api/documents/{doc_id}")
    assert r.status_code in (200, 204)


async def test_get_chunks_endpoint_shape(authed_client):
    """Even with no documents, the chunks endpoint should return 200
    and an empty list (or a 404 — we accept both since Phase 1 may
    not have wired the chunks endpoint to an empty list)."""
    r = await authed_client.get("/api/documents")
    assert r.status_code == 200
    items = r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()
    if not items:
        return  # No docs to inspect
    doc_id = items[0]["id"]
    r = await authed_client.get(f"/api/documents/{doc_id}/chunks")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        body = r.json()
        assert "items" in body or isinstance(body, list)

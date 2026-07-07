"""Smoke tests for the /model endpoint and the user-default connector
override. When the user has a default connector set, /model should
surface it instead of the env-var Ollama defaults.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.smoke


async def test_model_endpoint_exposes_built_in_when_no_default(unauth_client):
    r = await unauth_client.get("/model")
    assert r.status_code == 200
    data = r.json()
    # Without a user-default connector, the env-var Ollama config shows.
    assert data["provider"] == "ollama"


async def test_model_endpoint_includes_connector_id_field(unauth_client):
    """Per docs/api.md, /model should include `connector_id` (or null)
    so the UI can show which model answered."""
    r = await unauth_client.get("/model")
    assert r.status_code == 200
    data = r.json()
    assert "connector_id" in data  # null is fine

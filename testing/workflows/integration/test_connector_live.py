"""Integration tests for live external model connectors.

Gated behind:
  - `pytest.mark.integration` (live stack)
  - `ATHENA_TEST_CONNECTOR_ID` env var (a real, healthy connector)

If the env var is absent (the default), every test in this file is
skipped — this matches the user-confirmed design: "Ollama only by
default, opt-in for external connectors".
"""
from __future__ import annotations

import os
import uuid

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def _live_connector_id() -> str | None:
    return os.environ.get("ATHENA_TEST_CONNECTOR_ID")


@pytest.fixture
def live_connector_id():
    cid = _live_connector_id()
    if not cid:
        pytest.skip(
            "Set ATHENA_TEST_CONNECTOR_ID to run live-connector integration tests"
        )
    return cid


async def test_chat_with_live_connector_id(authed_client, live_connector_id):
    r = await authed_client.post(
        "/api/chat",
        json={
            "message": "Hi.",
            "conversation_id": None,
            "connector_id": live_connector_id,
        },
        timeout=180.0,
    )
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    # The model name should be surfaced (not None when a connector
    # was explicitly picked).
    assert msg.get("model") is not None
    # The connector_id should round-trip.
    assert msg.get("connector_id") == live_connector_id


async def test_chat_with_live_connector_and_model_hint(authed_client, live_connector_id):
    """The `model` field on the request should override the connector's
    default_model."""
    r = await authed_client.post(
        "/api/chat",
        json={
            "message": "Hi.",
            "connector_id": live_connector_id,
            "model": "gpt-4o-mini",
        },
        timeout=180.0,
    )
    assert r.status_code == 200
    msg = r.json()["message"]
    assert msg.get("model") == "gpt-4o-mini"

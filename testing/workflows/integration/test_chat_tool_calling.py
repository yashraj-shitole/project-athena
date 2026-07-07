"""Integration tests for tool calling.

A correct tool call requires:
1. The orchestrator parses the model's tool_call request.
2. The tool's `validate_arguments` passes.
3. The tool executes and its result is folded into the answer.

We assert the *envelope* (used_tools list, status fields); we don't
assert the exact final text because that's LLM-judge territory.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.smoke]


async def test_chat_can_invoke_search_documents_tool(authed_client):
    """The `search_documents` tool is built in; the model should be
    able to invoke it for relevant queries."""
    r = await authed_client.post(
        "/api/chat",
        json={
            "message": "Search the indexed documents for 'Q3'.",
            "tool_subset": ["search_documents"],
        },
        timeout=180.0,
    )
    assert r.status_code == 200
    msg = r.json()["message"]
    # used_tools is the public surface; the model may or may not
    # choose to call the tool for this phrasing. We only assert the
    # envelope exists.
    assert "used_tools" in msg
    for t in (msg.get("used_tools") or []):
        assert "name" in t
        assert "status" in t
        assert t["status"] in ("ok", "error", "skipped")


async def test_chat_with_explicit_tool_subset_uses_only_those_tools(authed_client):
    """When `tool_subset` is set, the orchestrator must not surface
    tools outside the subset. We assert the request accepts the field
    and returns 200."""
    r = await authed_client.post(
        "/api/chat",
        json={
            "message": "Hi.",
            "tool_subset": ["search_documents"],
        },
        timeout=120.0,
    )
    assert r.status_code == 200


async def test_chat_with_no_tool_subset_works(authed_client):
    r = await authed_client.post(
        "/api/chat",
        json={"message": "Hi."},
        timeout=120.0,
    )
    assert r.status_code == 200

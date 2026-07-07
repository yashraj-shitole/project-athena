"""Mimic the Anthropic /v1/messages wire shape for tests.

The streaming chunks match Anthropic's content_block_delta shape
(event: content_block_delta, data: {"delta": {"type": "text_delta",
"text": "..."}}).
"""
from __future__ import annotations

import json


def handler(request: httpx.Request) -> httpx.Response:  # type: ignore[name-defined]
    """Return a canned /v1/messages response."""
    return httpx.Response(  # type: ignore[name-defined]
        200,
        json={
            "id": "msg_mock",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi from anthropic mock"}],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 4},
        },
    )

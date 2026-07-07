"""An httpx MockTransport handler that emulates an OpenAI-compatible
chat-completions endpoint.

Use it in tests that exercise the chat-engine integration without
standing up a real provider:

    import httpx
    from testing.mocks.http.openai_mock import handler

    client = httpx.AsyncClient(
        base_url="https://fake-llm.example/v1",
        transport=httpx.MockTransport(handler),
    )
"""
from __future__ import annotations

import json
from typing import Any


def handler(request: httpx.Request) -> httpx.Response:  # type: ignore[name-defined]
    """Return a canned /chat/completions response.

    The handler reads the request body to set a 1-token echo in
    `prompt_tokens` so per-request usage varies.
    """
    try:
        body = json.loads(request.content)
    except Exception:
        body = {}
    msgs = body.get("messages", [])
    prompt_tokens = max(1, sum(len(str(m.get("content", "")).split()) for m in msgs))
    return httpx.Response(  # type: ignore[name-defined]
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": "hi from mock"}}
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 3,
                "total_tokens": prompt_tokens + 3,
            },
        },
    )

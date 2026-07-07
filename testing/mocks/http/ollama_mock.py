"""Mimic the Ollama /api/generate wire shape for tests."""
from __future__ import annotations

import json


def handler(request: httpx.Request) -> httpx.Response:  # type: ignore[name-defined]
    """Return a canned /api/generate response."""
    try:
        body = json.loads(request.content)
    except Exception:
        body = {}
    return httpx.Response(  # type: ignore[name-defined]
        200,
        json={
            "model": body.get("model", "qwen2.5:1.5b-instruct"),
            "response": "hi from ollama mock",
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 4,
        },
    )

"""A fake ProviderAdapter for the chat-engine integration tests.

Mirrors the real OpenAICompatibleProvider's wire shape, but without
making any HTTP calls. Use it as a drop-in for the router's fallback
in tests that want to exercise the LLMClient without standing up
Ollama.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


class FakeProvider:
    """A provider that returns canned chat + stream responses.

    Construct with a `chat_response` dict (and optionally a
    `stream_events` list). Both default to a hello-world response.
    """

    name = "fake"
    model = "fake-model"

    def __init__(
        self,
        chat_response: dict[str, Any] | None = None,
        stream_events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.chat_response = chat_response or {
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self.stream_events = stream_events or [
            {"choices": [{"delta": {"content": "hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}], "finish_reason": "stop"},
            {"done": True},
        ]
        self.closed = False

    async def chat(self, req) -> dict[str, Any]:
        return self.chat_response

    async def stream(self, req) -> AsyncIterator[dict[str, Any]]:
        for ev in self.stream_events:
            yield ev
            await asyncio.sleep(0)

    async def health_check(self) -> dict[str, Any]:
        return {"ok": True, "latency_ms": 1, "status": "online", "category": "ok"}

    async def aclose(self) -> None:
        self.closed = True


class FlakyProvider(FakeProvider):
    """A fake that fails N times in a row before succeeding.

    Use it to exercise the health probe's auto-disable logic."""

    def __init__(self, fail_times: int = 3, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_times = fail_times
        self.calls = 0

    async def chat(self, req) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            from app.services.providers.base import ProviderError, CAT_SERVER
            raise ProviderError("server_error", "simulated", category=CAT_SERVER, status_code=500)
        return await super().chat(req)

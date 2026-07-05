"""Async HTTP client for the Ollama chat API.

Used for both streaming and non-streaming turns. Tools (FR-29) are
exposed via Ollama's `tools` field.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class OllamaError(RuntimeError):
    """Raised when Ollama returns a non-2xx or malformed response."""


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(settings.OLLAMA_TIMEOUT_S, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]] | dict[str, Any]:
        """Single chat call. Returns the full dict (non-stream) or an async iterator of events."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options

        if not stream:
            r = await self._client.post("/api/chat", json=payload)
            if r.status_code != 200:
                raise OllamaError(f"ollama {r.status_code}: {r.text[:200]}")
            return r.json()
        return self._stream(payload)

    async def _stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        payload = {**payload, "stream": True}
        async with self._client.stream("POST", "/api/chat", json=payload) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise OllamaError(f"ollama {r.status_code}: {body[:200].decode(errors='replace')}")
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning("ollama.stream.bad_line", line=line[:200])
                    continue


_singleton: OllamaClient | None = None


def get_ollama() -> OllamaClient:
    global _singleton
    if _singleton is None:
        _singleton = OllamaClient()
    return _singleton


async def close_ollama() -> None:
    global _singleton
    if _singleton is not None:
        await _singleton.aclose()
        _singleton = None

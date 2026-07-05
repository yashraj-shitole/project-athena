"""High-level LLM client used by the orchestrator.

Wraps the raw `OllamaClient` with:
  - non-streaming `complete()` and streaming `stream()` helpers
  - structured response parsing (text + tool calls)
  - retry-once on empty output
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.ollama import OllamaClient, OllamaError, get_ollama

log = get_logger(__name__)
_settings = get_settings()


class LLMResponse:
    """Normalized LLM response (text + optional single tool call)."""

    __slots__ = ("text", "tool_call", "raw")

    def __init__(self, text: str = "", tool_call: dict | None = None, raw: Any = None):
        self.text = text
        self.tool_call = tool_call
        self.raw = raw


def _first_tool_call(message: dict) -> dict | None:
    """Ollama's chat response uses `message.tool_calls` (list) — normalize it."""
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    first = calls[0]
    fn = first.get("function") or {}
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    return {
        "name": fn.get("name") or first.get("name") or "",
        "arguments": args or {},
    }


def _build_options() -> dict[str, Any]:
    return {
        "temperature": 0.2,
        "num_ctx": _settings.TOKEN_BUDGET_TOTAL + 64,
        "num_predict": _settings.TOKEN_BUDGET_ANSWER,
    }


class LLMClient:
    def __init__(self, client: OllamaClient | None = None):
        self._client = client or get_ollama()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        options: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Non-streaming completion.

        Ollama errors are NOT swallowed: returning an empty response here
        would make `run_turn` silently persist an empty assistant message
        and answer 200 OK, hiding the outage from the user. Let it raise
        so `stream_turn` can emit RUN_ERROR and `run_turn` surfaces a
        real failure to the caller.
        """
        opts = options or _build_options()
        data = await self._client.chat(
            messages,
            tools=tools or None,
            stream=False,
            options=opts,
        )

        message = (data or {}).get("message") or {}
        text = message.get("content") or ""
        return LLMResponse(text=text, tool_call=_first_tool_call(message), raw=data)

    async def stream(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict]:
        """Yield text deltas as they arrive. Each yield is a dict
        {delta: str, done: bool}."""
        opts = options or _build_options()
        try:
            stream = await self._client.chat(
                messages,
                tools=tools or None,
                stream=True,
                options=opts,
            )
        except OllamaError as exc:
            yield {"delta": f"[llm error: {exc}]", "done": True, "error": str(exc)}
            return
        async for event in stream:  # type: ignore[union-attr]
            msg = (event or {}).get("message") or {}
            delta = msg.get("content") or ""
            done = bool(event.get("done"))
            yield {"delta": delta, "done": done}


_singleton: LLMClient | None = None


def get_llm() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    return _singleton


async def close_llm() -> None:
    global _singleton
    if _singleton is not None:
        await _singleton.aclose()
        _singleton = None


__all__ = ["LLMClient", "LLMResponse", "get_llm", "close_llm"]

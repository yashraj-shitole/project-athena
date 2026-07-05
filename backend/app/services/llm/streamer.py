"""SSE streamer shaping LLM events into AG-UI-style envelopes.

Event types (subset aligned with FR-26 / NFR-15):
  - RUN_STARTED       {run_id, conversation_id}
  - TEXT_MESSAGE_START  {message_id, role}
  - TEXT_MESSAGE_CONTENT {delta}
  - TOOL_CALL_START     {tool_call_id, tool_name, arguments (partial)}
  - TOOL_CALL_ARGS      {delta}
  - TOOL_CALL_END       {result, status, latency_ms}
  - TEXT_MESSAGE_END    {citations, used_tools}
  - RUN_FINISHED        {finish_reason}
  - RUN_ERROR           {error}

Wire format: `data: <json>\\n\\n` per SSE spec. The final event for a
run is always RUN_FINISHED (or RUN_ERROR).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Iterable

from app.core.logging import get_logger

log = get_logger(__name__)


def sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps({'type': event, **payload}, default=str)}\n\n".encode("utf-8")


def run_started(run_id: uuid.UUID, conversation_id: uuid.UUID) -> bytes:
    return sse("RUN_STARTED", {"run_id": str(run_id), "conversation_id": str(conversation_id)})


def run_finished(run_id: uuid.UUID, finish_reason: str = "stop") -> bytes:
    return sse("RUN_FINISHED", {"run_id": str(run_id), "finish_reason": finish_reason})


def run_error(run_id: uuid.UUID, error: str) -> bytes:
    return sse("RUN_ERROR", {"run_id": str(run_id), "error": error})


def text_message_start(message_id: uuid.UUID) -> bytes:
    return sse("TEXT_MESSAGE_START", {"message_id": str(message_id), "role": "assistant"})


def text_message_content(message_id: uuid.UUID, delta: str) -> bytes:
    return sse("TEXT_MESSAGE_CONTENT", {"message_id": str(message_id), "delta": delta})


def text_message_end(
    message_id: uuid.UUID,
    *,
    citations: Iterable[dict] | None = None,
    used_tools: Iterable[dict] | None = None,
) -> bytes:
    return sse(
        "TEXT_MESSAGE_END",
        {
            "message_id": str(message_id),
            "citations": list(citations or []),
            "used_tools": list(used_tools or []),
        },
    )


def tool_call_start(tool_call_id: uuid.UUID, tool_name: str) -> bytes:
    return sse(
        "TOOL_CALL_START",
        {"tool_call_id": str(tool_call_id), "tool_name": tool_name},
    )


def tool_call_args(tool_call_id: uuid.UUID, arguments: dict) -> bytes:
    return sse("TOOL_CALL_ARGS", {"tool_call_id": str(tool_call_id), "arguments": arguments})


def tool_call_end(
    tool_call_id: uuid.UUID,
    *,
    result: Any,
    status: str,
    latency_ms: int,
) -> bytes:
    return sse(
        "TOOL_CALL_END",
        {
            "tool_call_id": str(tool_call_id),
            "result": result,
            "status": status,
            "latency_ms": latency_ms,
        },
    )


async def passthrough(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """For when the caller just wants to forward SSE bytes verbatim."""
    async for chunk in stream:
        yield chunk

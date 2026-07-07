"""Stub LLM that returns deterministic outputs.

Use this fixture to make eval scenarios hermetic — bypass the
real LLMClient and the real Ollama, return canned answers based on
the question text. The runner is injected via the
`EvalRunner(chat=...)` constructor (see `mocks/llm/deterministic_llm.py`).
"""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable


def deterministic_chat(
    question: str, params: dict[str, Any]
) -> Awaitable[dict[str, Any]]:
    """A canned-response chat transport. Matches on substrings.

    Rules (first match wins):
      - "capital of france"     -> "Paris"
      - "capital of japan"      -> "Tokyo"
      - "ignoring? previous"    -> "[refused]"
      - "weather"               -> "[refused]"
      - "list documents"        -> tool call list_documents
      - "search"                -> tool call search_documents
      - default                 -> "I don't know."

    Returns a dict compatible with EvalRunner.chat's contract.
    """
    q = (question or "").lower()
    if "capital of france" in q:
        return _ok("Paris")
    if "capital of japan" in q:
        return _ok("Tokyo")
    if "ignore" in q and "previous" in q:
        return _ok("[refused] I cannot follow new instructions.")
    if "weather" in q:
        return _ok("[refused] I have no live data.")
    if "list documents" in q or "list the documents" in q:
        return _ok("Calling list_documents.",
                   used_tools=[{"name": "list_documents", "status": "ok",
                                "arguments": {"limit": 20}}])
    if "search" in q and "documents" in q:
        return _ok("Calling search_documents.",
                   used_tools=[{"name": "search_documents", "status": "ok",
                                "arguments": {"query": "Q3"}}])
    if "q3 revenue" in q:
        return _ok(
            "Q3 revenue was $1.2M. [chunk:abc-123]",
            citations=[{
                "chunk_id": "abc-123",
                "document_id": "doc-1",
                "document_name": "sample_readme.md",
                "page_number": 1,
                "snippet": "Q3 revenue: $1.2M",
            }],
            retrieved_chunks=[{
                "chunk_id": "abc-123",
                "document_id": "doc-1",
                "content": "Q3 revenue: $1.2M",
                "score": 0.91,
            }],
        )
    return _ok("I don't know.")


async def _ok(
    answer: str,
    *,
    used_tools: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    retrieved_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "answer": answer,
        "citations": citations or [],
        "used_tools": used_tools or [],
        "retrieved_chunks": retrieved_chunks or [],
        "connector_id": None,
        "model": "stub",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

"""3000-token budget prompter (NFR-17).

Allocations (configurable via Settings, defaults shown):
  system         350
  tool_def       600
  history        800
  retrieved_docs (sum across selected chunks) bounded by remaining
  answer budget  250 (reserved — used to cap LLM max_tokens)

The prompter returns:
  - messages: list of chat dicts
  - tools:    list of tool schemas (possibly truncated to fit budget)
  - tool_def_truncated: bool — surfaces to FR-35 snapshot
  - chunks_used: list[dict] of chunks that actually fit
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Sequence

from app.core.config import settings
from app.services.text import count_tokens, truncate_tokens

SYSTEM_PROMPT = (
    "You are Athena, a focused document-aware assistant.\n"
    "Use the provided context chunks and conversation history to answer.\n"
    "If the answer is not in the context, say you don't know and suggest what "
    "document would help. Always cite the chunk ids you used in the form "
    "[chunk:<id>] at the end of the relevant sentence.\n"
    "If a tool would help, call it via the tool-use JSON format exactly once "
    "per turn. Do not invent tool names or arguments."
)


@dataclass
class Prompt:
    messages: List[dict[str, Any]]
    tools: List[dict[str, Any]] = field(default_factory=list)
    chunks_used: List[dict] = field(default_factory=list)
    tool_def_truncated: bool = False
    history_truncated: bool = False
    chunks_truncated: bool = False
    total_tokens_est: int = 0


def _format_chunk(idx: int, chunk: dict, max_tokens: int) -> tuple[str, int]:
    """Render a single chunk to a context block bounded by `max_tokens`."""
    header = f"[chunk:{chunk['chunk_id']}] {chunk.get('document_name', '?')}"
    if chunk.get("page_number") is not None:
        header += f" p.{chunk['page_number']}"
    body = truncate_tokens(chunk["content"], max_tokens)
    block = f"{header}\n{body}"
    return block, count_tokens(block)


def build_prompt(
    *,
    query: str,
    chunks: Sequence[dict],
    history: Sequence[dict],
    tools: Sequence[dict],
    system_prompt: str | None = None,
) -> Prompt:
    """Assemble a prompt under the configured token budget."""
    budget = settings.TOKEN_BUDGET_TOTAL
    sys_text = system_prompt or SYSTEM_PROMPT
    sys_tokens = count_tokens(sys_text)
    if sys_tokens > settings.TOKEN_BUDGET_SYSTEM:
        sys_text = truncate_tokens(sys_text, settings.TOKEN_BUDGET_SYSTEM)
        sys_tokens = settings.TOKEN_BUDGET_SYSTEM

    # ---- Tool definitions (truncate by dropping lowest-priority tools)
    tool_blocks: list[dict] = []
    used = 0
    tool_def_truncated = False
    for t in tools:
        # Ollama/tool format: {"type":"function","function":{...}}
        rendered = t if "type" in t else {"type": "function", "function": t}
        t_tokens = count_tokens(str(rendered)) // 4  # rough; JSON strings are denser
        if used + t_tokens > settings.TOKEN_BUDGET_TOOL_DEF:
            tool_def_truncated = True
            break
        tool_blocks.append(rendered)
        used += t_tokens

    # ---- History (oldest dropped first)
    history_tokens = 0
    history_msgs: list[dict] = []
    history_truncated = False
    # We walk from most-recent backward, then reverse.
    for msg in reversed(list(history)):
        m_tokens = count_tokens(str(msg.get("content", "")))
        if history_tokens + m_tokens > settings.TOKEN_BUDGET_HISTORY:
            history_truncated = True
            break
        history_msgs.append(msg)
        history_tokens += m_tokens
    history_msgs.reverse()

    # ---- Retrieved chunks (sequential fill)
    remaining = budget - sys_tokens - used - history_tokens - count_tokens(query) - 32
    per_chunk = min(settings.TOKEN_BUDGET_CHUNK, max(64, remaining // max(1, len(chunks))))
    chunks_used: list[dict] = []
    chunks_truncated = False
    consumed = 0
    for i, ch in enumerate(chunks):
        if consumed + per_chunk > remaining:
            chunks_truncated = True
            break
        block, t = _format_chunk(i + 1, ch, per_chunk)
        if consumed + t > remaining:
            chunks_truncated = True
            break
        chunks_used.append({**ch, "_ctx": block})
        consumed += t

    # ---- Assemble messages
    user_parts: list[str] = [f"Question: {query.strip()}"]
    if chunks_used:
        joined = "\n\n".join(c["_ctx"] for c in chunks_used)
        user_parts.append("Context:\n" + joined)
    else:
        user_parts.append("(no context chunks available)")
    messages: list[dict] = [
        {"role": "system", "content": sys_text},
        *history_msgs,
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

    total = (
        sys_tokens
        + used
        + history_tokens
        + count_tokens(query)
        + consumed
    )
    return Prompt(
        messages=messages,
        tools=tool_blocks,
        chunks_used=chunks_used,
        tool_def_truncated=tool_def_truncated,
        history_truncated=history_truncated,
        chunks_truncated=chunks_truncated,
        total_tokens_est=total,
    )

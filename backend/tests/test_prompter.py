"""Tests for the orchestrator's prompter (NFR-17 token budget)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_chunk(i: int, n_chars: int) -> dict:
    return {
        "chunk_id": f"{i:08d}-0000-0000-0000-000000000000",
        "document_id": "00000000-0000-0000-0000-000000000099",
        "document_name": f"doc-{i}.txt",
        "page_number": 1,
        "content": "lorem ipsum " * (n_chars // 12),
        "keywords": [],
        "score": 0.5,
    }


def test_build_prompt_under_budget():
    from app.services.orchestrator.prompter import build_prompt

    chunks = [_make_chunk(i, 200) for i in range(4)]
    history = [{"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_documents",
                "description": "search docs",
                "parameters": {"type": "object"},
            },
        }
    ]
    p = build_prompt(query="What's in the doc?", chunks=chunks, history=history, tools=tools)
    assert p.messages
    assert p.messages[0]["role"] == "system"
    # Last message is the user
    assert p.messages[-1]["role"] == "user"
    # System message should contain "Athena"
    assert "Athena" in p.messages[0]["content"]


def test_extract_citations_basic():
    from app.services.orchestrator.prompter import build_prompt, extract_citations

    chunks = [
        {
            "chunk_id": "11111111-1111-1111-1111-111111111111",
            "document_id": "22222222-2222-2222-2222-222222222222",
            "document_name": "doc.txt",
            "page_number": 3,
            "content": "stuff",
            "keywords": [],
            "score": 0.5,
        }
    ]
    text = "Answer text [chunk:11111111-1111-1111-1111-111111111111] done."
    cites = extract_citations(text, chunks)
    assert len(cites) == 1
    assert cites[0]["chunk_id"] == "11111111-1111-1111-1111-111111111111"
    assert cites[0]["page_number"] == 3


def test_extract_citations_unknown_id():
    from app.services.orchestrator.prompter import extract_citations

    text = "[chunk:99999999-9999-9999-9999-999999999999]"
    assert extract_citations(text, []) == []


def test_chunks_truncated_when_too_many():
    from app.services.orchestrator.prompter import build_prompt

    # 50 fat chunks — the prompter should drop some.
    chunks = [_make_chunk(i, 2000) for i in range(50)]
    p = build_prompt(query="Q", chunks=chunks, history=[], tools=[])
    assert p.chunks_truncated
    assert len(p.chunks_used) < len(chunks)
    # Total tokens est should be under the configured budget
    from app.core.config import get_settings

    assert p.total_tokens_est <= get_settings().token_budget

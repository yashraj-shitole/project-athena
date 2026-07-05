"""Text utilities shared by ingestion and retrieval.

Functions here are intentionally pure (no I/O) so they can be reused,
tested cheaply, and called from background threads.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List

import tiktoken

from app.core.config import settings

_ENCODER = tiktoken.get_encoding("cl100k_base")

_WHITESPACE_RE = re.compile(r"\s+")
_NONPRINT_RE = re.compile(r"[^\x09\x0a\x0d\x20-\x7e\u00A0-\uFFFF]")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def count_tokens(text: str) -> int:
    """Accurate token count using the project's tiktoken encoder."""
    if not text:
        return 0
    return len(_ENCODER.encode(text, disallowed_special=()))


def truncate_tokens(text: str, max_tokens: int) -> str:
    """Trim text from the right until it fits within `max_tokens`."""
    if max_tokens <= 0:
        return ""
    ids = _ENCODER.encode(text, disallowed_special=())
    if len(ids) <= max_tokens:
        return text
    return _ENCODER.decode(ids[:max_tokens])


def clean_text(text: str) -> str:
    """Normalize whitespace, strip control characters, NFC unicode."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _NONPRINT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def split_sentences(text: str) -> List[str]:
    """Naive sentence splitter. Good enough for prose chunking."""
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_END_RE.split(text) if s.strip()]


def chunk_tokens_iter(text: str, target: int, overlap: int) -> Iterable[str]:
    """Yield ~target-token chunks with ~overlap-token overlap on token ids.

    This is the prose chunker core. It is intentionally simple — a
    sentence-aware version is layered on top by `chunker.py`.
    """
    if not text:
        return
    ids = _ENCODER.encode(text, disallowed_special=())
    if len(ids) <= target:
        yield text
        return
    step = max(1, target - overlap)
    for start in range(0, len(ids), step):
        piece = ids[start : start + target]
        if not piece:
            break
        yield _ENCODER.decode(piece)
        if start + target >= len(ids):
            break


def project_budget() -> dict[str, int]:
    """Return the active token budget snapshot (handy for logs/tests)."""
    return {
        "total": settings.TOKEN_BUDGET_TOTAL,
        "system": settings.TOKEN_BUDGET_SYSTEM,
        "tool_def": settings.TOKEN_BUDGET_TOOL_DEF,
        "history": settings.TOKEN_BUDGET_HISTORY,
        "chunk": settings.TOKEN_BUDGET_CHUNK,
        "answer": settings.TOKEN_BUDGET_ANSWER,
    }

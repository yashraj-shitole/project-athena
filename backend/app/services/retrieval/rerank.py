"""Reranking — pluggable, default is a no-op pass-through.

To enable a real cross-encoder reranker, set the rerank model name in
config and implement `_score` here. The interface is stable: given
(query, chunks) → same chunks, re-ordered.
"""
from __future__ import annotations

from typing import List

from app.core.logging import get_logger

log = get_logger(__name__)


def rerank(query: str, chunks: List[dict], top_k: int | None = None) -> List[dict]:
    """Default: identity reranker. Returns the same list, sliced to top_k."""
    if not chunks:
        return []
    out = list(chunks)
    if top_k is not None:
        out = out[:top_k]
    log.debug("rerank.identity", n=len(out))
    return out

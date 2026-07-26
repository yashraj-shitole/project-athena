"""Unit tests for the hybrid retrieval fixes.

Covers the highest-impact accuracy changes that had no unit coverage:
  * vector search runs (and rescues) when lexical returns 0 hits
  * the raw `query` is what gets embedded, not the keyword bag
  * vector hits below RETRIEVAL_VECTOR_MIN_SIM are dropped
  * `search.retrieve` routes through `rerank` and re-slices to top_k

The live lexical/vector/encode stack is exercised by the integration
suite; here we monkeypatch those seams so the fusion logic is tested
in isolation (per the test DB strategy: unit suite = stubs).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import List

import pytest

from app.services.retrieval import hybrid, search as retrieval_search


USER = uuid.uuid4()


class _Row:
    """Mimics a single embedding row: `.tolist()` -> List[float]."""

    def __init__(self, vals: List[float]) -> None:
        self._vals = vals

    def tolist(self) -> List[float]:
        return self._vals


class _FakeVec:
    """Mimics the shape `encode` returns: `.size` + `[i].tolist()`."""

    def __init__(self, items: List[List[float]]) -> None:
        self._items = [_Row(v) for v in items]

    @property
    def size(self) -> int:
        return len(self._items)

    def __getitem__(self, i: int):
        return self._items[i]


def _hit(chunk_id: str, score: float):
    return {
        "chunk_id": chunk_id,
        "document_id": "doc",
        "document_name": "doc.pdf",
        "page_number": 1,
        "content": "body",
        "keywords": [],
        "score": score,
    }


@pytest.mark.asyncio
async def test_vector_runs_when_lexical_empty(monkeypatch):
    """The #1 accuracy fix: lexical returning [] no longer suppresses vector."""
    encoded_query: list[str] = []

    async def fake_lexical(*a, **kw):
        return []  # lexical finds nothing

    async def fake_vector(*a, **kw):
        return [_hit("v1", 0.9), _hit("v2", 0.8)]

    def fake_encode(texts, _training):
        encoded_query.append(texts[0])
        return _FakeVec([[0.1] * 8])

    monkeypatch.setattr(hybrid.lexical, "search_lexical", fake_lexical)
    monkeypatch.setattr(hybrid.vector, "search_vector", fake_vector)
    monkeypatch.setattr(hybrid, "encode", fake_encode)

    out = await hybrid.hybrid_search(
        object(), user_id=USER, query="what is the policy on remote work", top_k=4
    )
    assert [h["chunk_id"] for h in out] == ["v1", "v2"]
    # The raw user message is embedded, not a keyword bag.
    assert encoded_query == ["what is the policy on remote work"]


@pytest.mark.asyncio
async def test_vector_min_sim_filters_irrelevant_hits(monkeypatch):
    async def fake_lexical(*a, **kw):
        return []

    async def fake_vector(*a, **kw):
        # One relevant, one below the 0.2 cosine floor.
        return [_hit("good", 0.6), _hit("bad", 0.05)]

    monkeypatch.setattr(hybrid.lexical, "search_lexical", fake_lexical)
    monkeypatch.setattr(hybrid.vector, "search_vector", fake_vector)
    monkeypatch.setattr(hybrid, "encode", lambda _t, _tr: _FakeVec([[0.1] * 8]))

    out = await hybrid.hybrid_search(object(), user_id=USER, query="q", top_k=4)
    assert [h["chunk_id"] for h in out] == ["good"]


@pytest.mark.asyncio
async def test_both_empty_returns_empty(monkeypatch):
    async def fake_lexical(*a, **kw):
        return []

    async def fake_vector(*a, **kw):
        return []

    monkeypatch.setattr(hybrid.lexical, "search_lexical", fake_lexical)
    monkeypatch.setattr(hybrid.vector, "search_vector", fake_vector)
    monkeypatch.setattr(hybrid, "encode", lambda _t, _tr: _FakeVec([[0.1] * 8]))

    out = await hybrid.hybrid_search(object(), user_id=USER, query="q", top_k=4)
    assert out == []


@pytest.mark.asyncio
async def test_fuses_when_both_have_hits(monkeypatch):
    async def fake_lexical(*a, **kw):
        return [_hit("lex1", 0.08), _hit("lex2", 0.07)]

    async def fake_vector(*a, **kw):
        return [_hit("lex1", 0.9), _hit("vec1", 0.85)]

    monkeypatch.setattr(hybrid.lexical, "search_lexical", fake_lexical)
    monkeypatch.setattr(hybrid.vector, "search_vector", fake_vector)
    monkeypatch.setattr(hybrid, "encode", lambda _t, _tr: _FakeVec([[0.1] * 8]))

    # Force both retrievers so RRF fusion is exercised (the default
    # confidence gate only runs vector when the lexical top score is
    # below RETRIEVAL_HYBRID_THRESHOLD=0.05; 0.08 is above it).
    out = await hybrid.hybrid_search(
        object(), user_id=USER, query="q", top_k=4, always_hybrid=True
    )
    ids = {h["chunk_id"] for h in out}
    assert ids == {"lex1", "lex2", "vec1"}


@pytest.mark.asyncio
async def test_retrieve_routes_through_rerank_and_reslices(monkeypatch):
    """search.retrieve must call rerank and re-slice to top_k."""
    fused = [_hit(f"c{i}", 0.5 - i * 0.01) for i in range(10)]

    async def fake_hybrid(*a, **kw):
        return list(fused)

    rerank_calls: list = []

    def fake_rerank(query, chunks, top_k=None):
        rerank_calls.append((top_k, len(chunks)))
        # Re-slice (mirrors the real identity reranker's contract).
        return list(chunks)[: top_k] if top_k is not None else list(chunks)

    monkeypatch.setattr(retrieval_search, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(retrieval_search, "rerank", fake_rerank)
    # Bypass the Redis cache.

    async def fake_get_json(*a, **k):
        return None

    async def fake_set_json(*a, **k):
        return None

    monkeypatch.setattr(retrieval_search, "get_json", fake_get_json)
    monkeypatch.setattr(retrieval_search, "set_json", fake_set_json)

    out = await retrieval_search.retrieve(
        session=object(),
        user_id=USER,
        keywords=["remote", "work"],
        query="what is the remote work policy",
        top_k=3,
    )
    assert len(out) == 3
    assert rerank_calls and rerank_calls[0][0] == 3
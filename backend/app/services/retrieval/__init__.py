"""Retrieval services.

Public surface (keep small):
  - search.retrieve: top-level cached hybrid retrieval
  - hybrid.hybrid_search: RRF fusion of lexical + vector
  - lexical.search_lexical / vector.search_vector: single-retriever access
  - rerank.rerank: pass-through reranker (swap with a cross-encoder later)
"""
from app.services.retrieval import hybrid, lexical, rerank, search, vector

__all__ = ["search", "hybrid", "lexical", "vector", "rerank"]

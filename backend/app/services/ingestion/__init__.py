"""Ingestion services. Public surface:
  - extractors: file-type aware text extraction
  - chunker: prose + tabular chunking
  - keywords: per-chunk keyword extraction (MMR-diversified)
  - store: bulk chunk persistence + RLS
  - pipeline: orchestrator (extract → chunk → embed → keywords → store)
"""
from app.services.ingestion import chunker, extractors, keywords, pipeline, store

# Backwards-compat alias: existing code may do `from app.services.ingestion import extract`.
# The implementation lives in `extractors.py` (renamed for clarity).
extract = extractors

__all__ = ["extractors", "chunker", "keywords", "store", "pipeline", "extract"]

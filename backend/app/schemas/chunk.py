"""Chunk + citation schemas (citations ride on assistant messages)."""
from __future__ import annotations

import uuid
from typing import Any, List

from pydantic import Field

from app.schemas.base import ORMModelBase


class Citation(ORMModelBase):
    """A pointer from an assistant message back to a source chunk.

    `score` is the retrieval score (BM25 / vector / RRF).
    `snippet` is a short preview truncated by the prompter to fit budget.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    page_number: int | None = None
    score: float
    snippet: str
    keywords: List[str] = Field(default_factory=list)


class ChunkPublic(ORMModelBase):
    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    keywords: List[str] = Field(default_factory=list)
    page_number: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

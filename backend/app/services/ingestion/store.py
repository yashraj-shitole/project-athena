"""Bulk persistence of chunks (and their embeddings) into Postgres.

This is the single I/O boundary for chunk writes. It:
  - sets the RLS GUC for the user (per-user isolation)
  - bulk-inserts chunks with embeddings in one transaction
  - invalidates the user's retrieval cache so stale hits don't surface
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable, List, Sequence

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_user
from app.core.config import settings
from app.core.database import set_rls_user
from app.core.logging import get_logger
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.services.ingestion.chunker import Chunk

log = get_logger(__name__)


async def delete_existing_chunks(
    session: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Wipe prior chunks for a document (idempotent re-ingest)."""
    await session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.user_id == user_id,
        )
    )


async def insert_chunks(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    chunks: Sequence[Chunk],
    embeddings: Sequence[Sequence[float]],
    keywords: Sequence[Sequence[str]],
) -> List[DocumentChunk]:
    """Insert all chunks + their vectors in a single transaction.

    RLS is set per-transaction. Vector type is handled by the
    `pgvector.sqlalchemy.Vector` column type.
    """
    if not chunks:
        return []
    await set_rls_user(session, user_id)

    await delete_existing_chunks(session, document_id, user_id)

    objs: list[DocumentChunk] = []
    for idx, (chunk, emb, kws) in enumerate(zip(chunks, embeddings, keywords)):
        objs.append(
            DocumentChunk(
                document_id=document_id,
                user_id=user_id,
                chunk_index=idx,
                content=chunk.content,
                embedding=list(emb),
                keywords=list(kws),
                page_number=chunk.page_number,
                row_range=chunk.row_range,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                meta=chunk.meta,
            )
        )
    session.add_all(objs)
    await session.flush()
    log.info(
        "store.chunks.inserted",
        document_id=str(document_id),
        count=len(objs),
    )
    return objs


async def mark_document_status(
    session: AsyncSession,
    document: Document,
    status: str,
    *,
    error_message: str | None = None,
    page_count: int | None = None,
    chunk_count: int | None = None,
    embedding_model: str | None = None,
    current_stage: str | None = None,
    stage_progress: dict | None = None,
    started_at: datetime | None = None,
    processed_at: datetime | None = None,
    processing_time_ms: int | None = None,
) -> None:
    """Update a document row's lifecycle status (and any side metadata).

    All extra fields are keyword-only and default-None so this stays
    drop-in compatible with the call sites that previously passed just
    `(session, document, status)`. `None` means "leave alone" — a
    non-None value overwrites the column. `stage_progress` is a dict
    that replaces the JSONB column wholesale (callers pass the
    full per-stage map, not a diff).
    """
    document.status = status
    if error_message is not None:
        document.error_message = error_message
    if page_count is not None:
        document.page_count = page_count
    if chunk_count is not None:
        document.chunk_count = chunk_count
    if embedding_model is not None:
        document.embedding_model = embedding_model
    if current_stage is not None:
        document.current_stage = current_stage
    if stage_progress is not None:
        document.stage_progress = stage_progress
    if started_at is not None:
        document.started_at = started_at
    if processed_at is not None:
        document.processed_at = processed_at
    if processing_time_ms is not None:
        document.processing_time_ms = processing_time_ms
    await session.flush()


async def mark_document_progress(
    session: AsyncSession,
    document: Document,
    *,
    current_stage: str,
    stage_progress: dict,
    chunk_count: int | None = None,
    page_count: int | None = None,
    started_at: datetime | None = None,
) -> None:
    """Update only the mid-pipeline progress fields.

    Unlike `mark_document_status`, this NEVER changes `status` — the
    doc stays in `processing` (or whatever it was) while we tick the
    stage and percentage columns. `stage_progress` replaces the column
    wholesale (it's a small dict). `chunk_count`/`page_count` are
    accepted as a convenience for the moments they become known mid-
    pipeline.
    """
    document.current_stage = current_stage
    document.stage_progress = stage_progress
    if chunk_count is not None:
        document.chunk_count = chunk_count
    if page_count is not None:
        document.page_count = page_count
    if started_at is not None:
        document.started_at = started_at
    await session.flush()


async def finalize_indexing(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    """After a document is indexed, drop its retrieval cache entries."""
    await invalidate_user(user_id, prefix=settings.CACHE_PREFIX_RETRIEVAL)

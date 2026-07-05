"""Bulk persistence of chunks (and their embeddings) into Postgres.

This is the single I/O boundary for chunk writes. It:
  - sets the RLS GUC for the user (per-user isolation)
  - bulk-inserts chunks with embeddings in one transaction
  - invalidates the user's retrieval cache so stale hits don't surface
"""
from __future__ import annotations

import uuid
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
    error_message: str | None = None,
    page_count: int | None = None,
) -> None:
    """Update a document row's lifecycle status. Reusable for any state change."""
    document.status = status
    if error_message is not None:
        document.error_message = error_message
    if page_count is not None:
        document.page_count = page_count
    await session.flush()


async def finalize_indexing(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    """After a document is indexed, drop its retrieval cache entries."""
    await invalidate_user(user_id, prefix=settings.CACHE_PREFIX_RETRIEVAL)

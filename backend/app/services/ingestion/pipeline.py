"""Ingestion pipeline orchestrator.

Coordinates: extract → clean → chunk → embed → keyword-extract → store.
Reports lifecycle via a pluggable `status_cb` (used by the API for
ingest progress, FR-4).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.document import Document
from app.services.embedding import encode
from app.services.ingestion.extractors import extract as extract_text
from app.services.ingestion import chunker, store
from app.services.ingestion.keywords import extract_keywords

log = get_logger(__name__)

StatusCb = Callable[[str, dict], Awaitable[None]]


async def _noop_status(_status: str, _payload: dict) -> None:
    return


async def ingest_document(
    session: AsyncSession,
    *,
    document: Document,
    file_path: Path,
    status_cb: Optional[StatusCb] = None,
) -> int:
    """Run the full ingestion pipeline for a single document.

    Returns the number of chunks indexed.
    """
    cb = status_cb or _noop_status
    user_id = document.user_id
    document_id = document.id

    try:
        await cb("processing", {"document_id": str(document_id)})
        await store.mark_document_status(session, document, "processing")

        # 1. Extract
        result = extract_text(file_path)
        page_count = result.meta.get("pages") if result.mode == "prose" else None
        if page_count is not None:
            await store.mark_document_status(session, document, "processing", page_count=page_count)

        # 2. Chunk
        chunks = chunker.chunk(result)
        if not chunks:
            raise ValueError("No extractable text found in document")

        # 3. Embed (batch)
        texts = [c.content for c in chunks]
        vecs = encode(texts, normalize=True)
        # Convert numpy → python lists (row per chunk)
        emb_list: list[list[float]] = [v.tolist() for v in vecs]

        # 4. Keywords (uses chunk embedding for on-topic filter)
        kws_list: list[list[str]] = [
            extract_keywords(c.content, chunk_embedding=emb, top_k=8)
            for c, emb in zip(chunks, emb_list)
        ]

        # 5. Persist
        await store.insert_chunks(
            session,
            document_id=document_id,
            user_id=user_id,
            chunks=chunks,
            embeddings=emb_list,
            keywords=kws_list,
        )
        await store.mark_document_status(session, document, "indexed", page_count=page_count)
        await store.finalize_indexing(session, user_id)
        await session.commit()

        await cb("indexed", {"document_id": str(document_id), "chunks": len(chunks)})
        log.info(
            "pipeline.done",
            document_id=str(document_id),
            chunks=len(chunks),
        )
        return len(chunks)

    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        log.error(
            "pipeline.failed",
            document_id=str(document_id),
            error=str(exc),
        )
        try:
            await store.mark_document_status(
                session, document, "failed", error_message=str(exc)[:500]
            )
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()
        await cb("failed", {"document_id": str(document_id), "error": str(exc)})
        raise

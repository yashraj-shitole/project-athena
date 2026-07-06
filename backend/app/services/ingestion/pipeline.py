"""Ingestion pipeline orchestrator.

Coordinates: extract → clean → chunk → embed → keyword-extract → store.
Reports lifecycle via a pluggable `status_cb` (used by the API for
ingest progress, FR-4).

The pipeline's `status_cb` signature:
    await cb(event: str, payload: dict) -> None

Where `event` is one of the lifecycle names below, and the payload
mirrors what the API layer needs to (a) persist progress to the DB
and (b) fan out an SSE event. The pipeline itself does NOT touch
SSE or the event bus — that mapping lives in `app.api.documents`
where the cb is constructed. Keeping the pipeline agnostic of the
broadcast channel makes it straightforward to drive from a CLI or
a worker.

Lifecycle events emitted, in order:
  - "processing"  payload={document_id}              pipeline started
  - "extracted"   payload={document_id, page_count}  text is in hand
  - "chunked"     payload={document_id, chunk_count} chunks are ready
  - "embedding"   payload={document_id, current, total, percent}
                                                      mid-batch
  - "embedded"    payload={document_id, embedded_count, total}
                                                      all vectors ready
  - "indexed"     payload={document_id, chunks, embedding_model, processing_time_ms}
                                                      final success
  - "failed"      payload={document_id, error, processing_time_ms}
                                                      final failure

`processing_time_ms` is included on the final `indexed`/`failed`
events so the API closure can persist it without re-deriving it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.document import Document
from app.services.embedding import encode_batched
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

    Returns the number of chunks indexed. The pipeline never
    explicitly re-stamps `started_at` if it was already set — that
    allows `/retry` to preserve the original wall-clock for "time
    spent in failure state" accounting, while the API closure resets
    the column on a fresh retry request.
    """
    cb = status_cb or _noop_status
    user_id = document.user_id
    document_id = document.id

    # `started_at` is stamped once at the top so a single doc has one
    # canonical pipeline-start timestamp, even if individual stages
    # come and go. The `or now` guard means a retry that doesn't
    # reset the column keeps the original start (the API closure
    # explicitly resets it on retry if it wants a fresh window).
    if document.started_at is None:
        document.started_at = datetime.now(timezone.utc)

    def _elapsed_ms() -> int:
        if document.started_at is None:
            return 0
        delta = datetime.now(timezone.utc) - document.started_at
        return max(0, int(delta.total_seconds() * 1000))

    try:
        await cb("processing", {"document_id": str(document_id)})
        await store.mark_document_status(
            session, document, "processing",
            current_stage="extracting",
            stage_progress={"extracting": 0},
        )

        # 1. Extract
        result = extract_text(file_path)
        page_count = result.meta.get("pages") if result.mode == "prose" else None

        await cb(
            "extracted",
            {
                "document_id": str(document_id),
                "page_count": page_count,
            },
        )
        await store.mark_document_status(
            session, document, "processing",
            current_stage="chunking",
            stage_progress={"extracting": 100, "chunking": 0},
            page_count=page_count,
        )

        # 2. Chunk
        chunks = chunker.chunk(result)
        if not chunks:
            raise ValueError("No extractable text found in document")
        chunk_count = len(chunks)

        await cb(
            "chunked",
            {
                "document_id": str(document_id),
                "chunk_count": chunk_count,
            },
        )
        await store.mark_document_status(
            session, document, "processing",
            current_stage="embedding",
            stage_progress={"extracting": 100, "chunking": 100, "embedding": 0},
            chunk_count=chunk_count,
        )

        # 3. Embed (batched, with per-batch progress).
        texts = [c.content for c in chunks]

        async def _on_embed_batch(current: int, total: int) -> None:
            pct = int(round(current * 100 / total)) if total else 100
            await cb(
                "embedding",
                {
                    "document_id": str(document_id),
                    "current": current,
                    "total": total,
                    "percent": pct,
                },
            )
            # Persist progress in-place. We re-stamp the same
            # `current_stage` so the row is "fresh" for anyone polling
            # while the embedder is still ticking.
            await store.mark_document_progress(
                session, document,
                current_stage="embedding",
                stage_progress={
                    "extracting": 100,
                    "chunking": 100,
                    "embedding": pct,
                },
            )

        vecs = await encode_batched(texts, normalize=True, on_batch=_on_embed_batch)
        # Convert numpy → python lists (row per chunk)
        emb_list: list[list[float]] = [v.tolist() for v in vecs]

        await cb(
            "embedded",
            {
                "document_id": str(document_id),
                "embedded_count": len(emb_list),
                "total": chunk_count,
            },
        )
        await store.mark_document_status(
            session, document, "processing",
            current_stage="indexing",
            stage_progress={
                "extracting": 100,
                "chunking": 100,
                "embedding": 100,
                "indexing": 0,
            },
        )

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

        # Final terminal state.
        document.processed_at = datetime.now(timezone.utc)
        processing_time_ms = _elapsed_ms()
        await store.mark_document_status(
            session, document, "indexed",
            current_stage="completed",
            stage_progress={
                "extracting": 100,
                "chunking": 100,
                "embedding": 100,
                "indexing": 100,
            },
            chunk_count=chunk_count,
            embedding_model=settings.EMBED_MODEL_NAME,
            processed_at=document.processed_at,
            processing_time_ms=processing_time_ms,
        )
        await store.finalize_indexing(session, user_id)
        await session.commit()

        await cb(
            "indexed",
            {
                "document_id": str(document_id),
                "chunks": chunk_count,
                "embedding_model": settings.EMBED_MODEL_NAME,
                "processing_time_ms": processing_time_ms,
            },
        )
        log.info(
            "pipeline.done",
            document_id=str(document_id),
            chunks=chunk_count,
            processing_time_ms=processing_time_ms,
        )
        return chunk_count

    except Exception as exc:  # noqa: BLE001
        # Snapshot the values we need from the ORM BEFORE rollback.
        # After `session.rollback()`, every column on `document` is
        # expired — any further attribute read in this coroutine
        # would attempt a lazy refresh in the async context and
        # raise `greenlet_spawn` (xd2s). We can't reliably touch
        # the ORM here at all, so the failure-path DB write goes
        # through Core `update()` and uses only values we already
        # have in local variables.
        started_at = document.started_at  # read pre-rollback
        await session.rollback()
        # Compute elapsed from the snapshot rather than via
        # `_elapsed_ms()` — the latter reads `document.started_at`
        # which is now expired (this was the original bug that
        # surfaced as `greenlet_spawn` after a `.doc` upload).
        now = datetime.now(timezone.utc)
        processing_time_ms = 0
        if started_at is not None:
            processing_time_ms = max(
                0, int((now - started_at).total_seconds() * 1000)
            )
        log.error(
            "pipeline.failed",
            document_id=str(document_id),
            error=str(exc),
            processing_time_ms=processing_time_ms,
        )
        try:
            from sqlalchemy import update as sa_update

            await session.execute(
                sa_update(Document)
                .where(Document.id == document_id)
                .values(
                    status="failed",
                    current_stage="failed",
                    stage_progress={},
                    error_message=str(exc)[:500],
                    started_at=started_at,
                    processed_at=now,
                    processing_time_ms=processing_time_ms,
                )
            )
            await session.commit()
        except Exception as mark_exc:  # noqa: BLE001
            await session.rollback()
            log.error(
                "pipeline.mark_failed",
                document_id=str(document_id),
                error=str(mark_exc),
            )
        await cb(
            "failed",
            {
                "document_id": str(document_id),
                "error": str(exc),
                "processing_time_ms": processing_time_ms,
            },
        )
        raise

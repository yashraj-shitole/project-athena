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
                  (the count comes from a fast count pass over
                  the chunker; for the UI's progress bar the
                  per-batch "embedding" event below is what
                  drives the visual.)
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

Streaming design
----------------
The chunker is a generator; the pipeline drives it in fixed-size
batches. For each batch we:

  1. Embed the chunk texts via `encode_batched` (already off the
     event loop via `asyncio.to_thread` internally).
  2. Collect every chunk's candidate keyword phrases.
  3. Encode *all* candidates in a single `encode()` call (off the
     event loop). This collapses N forward passes into 1 — a 5–10×
     win on the "indexing" stage, which used to dominate CSV
     ingestion.
  4. Slice the resulting matrix back per-chunk and run
     `select_keywords` (pure CPU).
  5. `copy_chunks` (asyncpg binary COPY on Postgres; ORM fallback
     otherwise) for this batch. The transaction spans the whole
     pipeline, so all batches share one commit at the end.

Peak memory is bounded by ``batch_size × text + batch_size ×
candidate_phrase_length`` — not the full document.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import set_rls_user
from app.core.logging import get_logger
from app.models.document import Document
from app.services.embedding import encode, encode_batched
from app.services.ingestion.extractors import extract as extract_text
from app.services.ingestion import chunker, store
from app.services.ingestion.keywords import (
    candidate_phrases,
    select_keywords,
)

log = get_logger(__name__)

StatusCb = Callable[[str, dict], Awaitable[None]]


async def _noop_status(_status: str, _payload: dict) -> None:
    return


_DEFAULT_EMBED_BATCH = 32


def _sync_batched(items, size: int):
    """Synchronous batched(): group an iterable into lists of up to
    `size` items. Used when we have a sync generator from
    `chunker.iter_chunks`."""
    it = iter(items)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            return
        yield chunk


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

        # 1. Extract — wrap the synchronous parser in `to_thread`
        # so the chat SSE loop stays responsive while a 25MB PDF
        # is being parsed. The `ExtractionResult` (text + tables)
        # is small enough to pass between threads by reference.
        result = await asyncio.to_thread(extract_text, file_path)
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

        # 2. Chunk — get the full count up-front so the per-batch
        # progress event has a real `total`. We use a fast count
        # pass that throws away the chunks after counting; the
        # generator is then re-entered for the actual streaming
        # embed loop. The count pass is cheap (regex + token
        # counting) and bounded by document size; for a 25MB doc
        # it adds <100ms over the streaming loop's total cost.
        chunk_iter = chunker.iter_chunks(result)
        chunk_count = sum(1 for _ in chunk_iter)
        if chunk_count == 0:
            raise ValueError("No extractable text found in document")

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

        # 3 + 4 + 5. Stream: embed + keywords (batched) + insert (COPY).
        # We re-enter the chunker — the count pass exhausted the
        # generator. `iter_chunks` is a pure function; calling it
        # again is O(n) but cheap.
        chunk_iter = chunker.iter_chunks(result)
        # `indexing` is the keyword-encoding + COPY stage. We
        # start it conceptually now (the API's stage_progress
        # only ticks per-batch during the embed+index loop).
        batch_size = settings.INGEST_EMBED_BATCH_SIZE or _DEFAULT_EMBED_BATCH
        total_batches = (chunk_count + batch_size - 1) // batch_size
        batch_index = 0
        embedded_count = 0
        # `delete_existing_chunks` is idempotent re-ingest
        # cleanup; we run it once before the loop. RLS GUC is
        # set on the same connection before the first COPY (the
        # asyncpg `copy_records_to_table` path bypasses the
        # SQLAlchemy ORM hooks, so the session-level RLS setter
        # from the ORM fallback in `store.copy_chunks` doesn't
        # fire on the PG fast path).
        await set_rls_user(session, user_id)
        await store.delete_existing_chunks(session, document_id, user_id)

        for batch_chunks in _sync_batched(chunk_iter, batch_size):
            batch_index += 1
            batch_texts = [c.content for c in batch_chunks]

            # Per-batch progress (SSE PROGRESS, DB row update).
            pct = int(round(batch_index * 100 / total_batches)) if total_batches else 100
            await cb(
                "embedding",
                {
                    "document_id": str(document_id),
                    "current": batch_index,
                    "total": total_batches,
                    "percent": pct,
                },
            )
            await store.mark_document_progress(
                session, document,
                current_stage="embedding",
                stage_progress={
                    "extracting": 100,
                    "chunking": 100,
                    "embedding": pct,
                },
            )

            # 3. Embed this batch's chunks (already batched + off
            # the event loop by `encode_batched`).
            vecs = await encode_batched(batch_texts, normalize=True)
            # Convert numpy → python lists (row per chunk).
            emb_list: list[list[float]] = [v.tolist() for v in vecs]

            # 4. Keywords — batched: gather every chunk's
            # candidate phrases, encode them all in one call,
            # slice back per-chunk. For 32 chunks × 8 candidates
            # this is one forward pass instead of 32.
            batch_phrases_per_chunk: list[list[str]] = [
                candidate_phrases(text) for text in batch_texts
            ]
            flat_phrases: list[str] = []
            boundaries: list[int] = [0]
            for phrases in batch_phrases_per_chunk:
                flat_phrases.extend(phrases)
                boundaries.append(len(flat_phrases))
            if flat_phrases:
                cand_vecs = await asyncio.to_thread(
                    encode, flat_phrases, True
                )
            else:
                cand_vecs = None

            kws_list: list[list[str]] = []
            for chunk_text, chunk_emb, phrases, start, end in zip(
                batch_texts,
                emb_list,
                batch_phrases_per_chunk,
                boundaries[:-1],
                boundaries[1:],
            ):
                if not phrases or cand_vecs is None:
                    kws_list.append([])
                    continue
                kws_list.append(
                    select_keywords(
                        phrases,
                        cand_vecs=cand_vecs[start:end],
                        chunk_embedding=chunk_emb,
                        top_k=8,
                    )
                )

            # 5. Persist this batch via asyncpg COPY (or the ORM
            # fallback for non-Postgres dialects). The transaction
            # is shared across all batches; we commit once at the
            # end of the pipeline.
            await store.copy_chunks(
                session,
                document_id=document_id,
                user_id=user_id,
                chunks=batch_chunks,
                embeddings=emb_list,
                keywords=kws_list,
            )
            embedded_count += len(batch_chunks)

        await cb(
            "embedded",
            {
                "document_id": str(document_id),
                "embedded_count": embedded_count,
                "total": chunk_count,
            },
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

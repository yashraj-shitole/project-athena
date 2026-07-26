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
batches. Embedding is the dominant cost (~99% of wall-clock on large
docs), and the MiniLM encoder releases the GIL during the torch
matmul, so we fan `ingest_embed_workers` batch forward-passes across
worker threads (sliding window of in-flight embed tasks). For each
batch, in order, we then:

  1. Await that batch's embedding (it has been running in a thread
     since it entered the window, possibly already done).
  2. Collect every chunk's candidate keyword phrases.
  3. Encode *all* candidates in a single `encode()` call (off the
     event loop). This collapses N forward passes into 1 — a 5–10×
     win on the "indexing" stage, which used to dominate CSV
     ingestion.
  4. Slice the resulting matrix back per-chunk and run
     `select_keywords` (pure CPU).
  5. `copy_chunks` (asyncpg text COPY on Postgres; ORM fallback
     otherwise) for this batch. The transaction spans the whole
     pipeline, so all batches share one commit at the end.

Keyword-encode + COPY stay strictly sequential — they're <1% of the
cost and not safe to run concurrently on the single shared DB
connection. The next batch's embed is scheduled before step 2 so its
matmul overlaps with this batch's keyword-encode + COPY.

Peak memory is bounded by ``workers × batch_size × text`` — not the
full document.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import set_rls_user
from app.core.logging import get_logger
from app.models.document import Document
from app.services.embedding import (
    _encode_one_batch,
    encode,
    resolve_embed_workers,
    tune_torch_threads_for_workers,
)
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

        # Per-stage wall-clock (monotonic). Logged on the terminal
        # `indexed` event so operators can see where time goes in the
        # live app — not just via the perf bench. Stages match the
        # bench's derivation: extracting = processing→extracted,
        # chunking = extracted→chunked, embedding = chunked→embedded
        # (the whole embed+keyword+COPY loop), indexing = embedded→
        # indexed (the final commit).
        _t0 = time.monotonic()

        # 1. Extract — wrap the synchronous parser in `to_thread`
        # so the chat SSE loop stays responsive while a 25MB PDF
        # is being parsed. The `ExtractionResult` (text + tables)
        # is small enough to pass between threads by reference.
        result = await asyncio.to_thread(extract_text, file_path)
        _t_extract = time.monotonic()
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
        _t_chunk = time.monotonic()
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
        # Tune HNSW bulk-load for this transaction (SET LOCAL — resets
        # at the final commit). Lower ef_insert + a bigger
        # maintenance_work_mem make the per-batch COPY's HNSW index
        # maintenance cheaper; retrieval ef_search is untouched. No-op
        # on SQLite.
        await store.set_ingest_bulk_load_gucs(session)

        # --- Parallel embedding, sequential keyword-encode + COPY -------
        # Embedding is ~99% of ingestion wall-clock and the MiniLM
        # encoder releases the GIL during the torch matmul, so we fan
        # `workers` batch forward-passes across worker threads for a
        # near-linear speedup up to core count. Keyword-encode + COPY
        # stay strictly sequential: they're <1% of the cost and not
        # safe to run concurrently on the single shared DB connection.
        #
        # We keep a sliding window of at most `workers` in-flight embed
        # tasks (deque of (chunks, texts, asyncio.Task)). Each turn:
        # pop the oldest, report progress, await its vector (it has
        # been running in a thread while we processed the previous
        # batch's keywords + COPY), then schedule the next batch's
        # embed to refill the window. Peak memory stays bounded at
        # `workers × batch_size` chunks — not the whole document.
        workers = resolve_embed_workers()
        tune_torch_threads_for_workers(workers)

        async def _schedule_embed(
            batch_chunks: list,
        ) -> tuple[list, list[str], "asyncio.Task"]:
            texts = [c.content for c in batch_chunks]
            task = asyncio.create_task(
                asyncio.to_thread(_encode_one_batch, texts, True)
            )
            return batch_chunks, texts, task

        chunk_seq = iter(chunk_iter)
        window: deque = deque()
        # Prime the window with up to `workers` batches.
        for _ in range(workers):
            nxt = list(islice(chunk_seq, batch_size))
            if not nxt:
                break
            window.append(await _schedule_embed(nxt))

        # Accumulators for the DB-side cost, logged on completion so
        # we can tell compute-bound from DB-bound ingestion:
        #   copy_ms_total        — asyncpg COPY of chunk rows (includes
        #     tsvector generation + HNSW/GIN index maintenance).
        #   flush_ms_total       — UPDATE of the documents progress row.
        #   embed_compute_ms_total — time awaiting chunk-vector embed
        #     tasks (pure encoder forward-passes; GIL-released matmul).
        #   keyword_ms_total     — keyword candidate encode + MMR select
        #     (the encoder cost added by keyword extraction).
        copy_ms_total = 0.0
        flush_ms_total = 0.0
        embed_compute_ms_total = 0.0
        keyword_ms_total = 0.0
        # Persist the documents progress row only when the integer
        # percent crosses a 5% bucket (and on the final batch). The
        # SSE event still fires every batch (cheap, in-process), so
        # the UI progress bar keeps ticking; this just bounds the
        # number of UPDATE round-trips to ~20 regardless of doc size
        # instead of one per batch — those round-trips can't overlap
        # with the parallel embeds and become the pacing cost once
        # embedding compute is fast.
        _PROGRESS_BUCKET = 5
        last_flushed_bucket = -1

        while window:
            batch_chunks, batch_texts, embed_task = window.popleft()
            batch_index += 1

            # 3. Embed this batch's chunks FIRST. The forward pass has
            # been running in a worker thread since the batch entered
            # the window (possibly already done). Consuming it before
            # the per-batch DB work (and before scheduling the next
            # batch) keeps the embed threads busy and stops the
            # sequential progress flush + COPY from gating the next
            # matmul — the key to overlapping embed-CPU with DB-I/O.
            _t_embed_await = time.monotonic()
            vecs = await embed_task
            embed_compute_ms_total += time.monotonic() - _t_embed_await
            # Convert numpy → python lists (row per chunk). One call
            # on the 2D array beats per-row `.tolist()` boxing.
            emb_list: list[list[float]] = vecs.tolist()

            # Refill the window: schedule the next batch's embed now
            # so it overlaps with this batch's keyword-encode + COPY.
            nxt = list(islice(chunk_seq, batch_size))
            if nxt:
                window.append(await _schedule_embed(nxt))

            # Per-batch progress: SSE every batch (cheap, in-process),
            # documents row only on a 5% bucket boundary (or final).
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
            bucket = (pct // _PROGRESS_BUCKET) * _PROGRESS_BUCKET
            if bucket != last_flushed_bucket or batch_index == total_batches:
                last_flushed_bucket = bucket
                _t_flush = time.monotonic()
                await store.mark_document_progress(
                    session, document,
                    current_stage="embedding",
                    stage_progress={
                        "extracting": 100,
                        "chunking": 100,
                        "embedding": pct,
                    },
                )
                flush_ms_total += time.monotonic() - _t_flush

            # 4. Keywords — batched: gather every chunk's
            # candidate phrases, encode them all in one call,
            # slice back per-chunk. For 32 chunks × 8 candidates
            # this is one forward pass instead of 32.
            _t_keyword = time.monotonic()
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
            keyword_ms_total += time.monotonic() - _t_keyword

            # 5. Persist this batch via asyncpg COPY (or the ORM
            # fallback for non-Postgres dialects). The transaction
            # is shared across all batches; we commit once at the
            # end of the pipeline.
            _t_copy = time.monotonic()
            await store.copy_chunks(
                session,
                document_id=document_id,
                user_id=user_id,
                chunks=batch_chunks,
                embeddings=emb_list,
                keywords=kws_list,
            )
            copy_ms_total += time.monotonic() - _t_copy
            embedded_count += len(batch_chunks)

        _t_embed = time.monotonic()
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
        _t_index = time.monotonic()

        # Sub-stage split of the embedding stage (embed compute vs
        # keyword encode vs DB COPY vs progress flush). Surfaced on the
        # `indexed` event so the perf bench (and any consumer) can see
        # whether ingestion is DB-bound or encoder-bound. The API
        # status-cb closure reads only `chunks`/`embedding_model`/
        # `processing_time_ms` via payload.get(), so the extra keys are
        # ignored by SSE — backward-compatible.
        sub_ms = {
            "embed_ms": int(embed_compute_ms_total * 1000),
            "keyword_ms": int(keyword_ms_total * 1000),
            "copy_ms": int(copy_ms_total * 1000),
            "flush_ms": int(flush_ms_total * 1000),
        }
        await cb(
            "indexed",
            {
                "document_id": str(document_id),
                "chunks": chunk_count,
                "embedding_model": settings.EMBED_MODEL_NAME,
                "processing_time_ms": processing_time_ms,
                **sub_ms,
            },
        )
        # Per-stage breakdown so operators can see where time goes in
        # the live app (matches the perf-bench stage derivation). If
        # embedding still dominates, the next levers are a bigger
        # batch / more workers / an off-CPU encoder.
        stages_ms = {
            "extracting_ms": int((_t_extract - _t0) * 1000),
            "chunking_ms": int((_t_chunk - _t_extract) * 1000),
            "embedding_ms": int((_t_embed - _t_chunk) * 1000),
            "indexing_ms": int((_t_index - _t_embed) * 1000),
            # Sub-stage split of the embedding stage — if `copy_ms` is a
            # large fraction of `embedding_ms`, ingestion is DB-bound
            # (chunk COPY + HNSW/GIN/tsvector maintenance); if
            # `keyword_ms` dominates, it's encoder-bound and the next
            # lever is the keyword path, not the DB.
            **sub_ms,
        }
        log.info(
            "pipeline.done",
            document_id=str(document_id),
            chunks=chunk_count,
            workers=resolve_embed_workers(),
            batch_size=settings.INGEST_EMBED_BATCH_SIZE,
            processing_time_ms=processing_time_ms,
            **stages_ms,
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

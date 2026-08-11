"""Regression test for the parallel-embedding ingestion loop.

The pipeline fans `ingest_embed_workers` batch forward-passes across
worker threads (sliding window of in-flight embed tasks). This test
verifies the restructure didn't change the externally observable
contract, and that the parallelism is real:

  * every chunk is embedded exactly once,
  * every chunk is persisted exactly once,
  * chunks are persisted in the same order the chunker emits them,
  * embed calls actually overlap in time (>=2 in flight at once) when
    workers > 1.

Hermetic: no DB, no real model. We stub `_encode_one_batch`, the
`store.*` helpers, `set_rls_user`, and `candidate_phrases` (so the
keyword branch is skipped — it's covered elsewhere and orthogonal to
the embed loop). The real extractor + chunker run on a small temp txt
file so the loop sees genuine `Chunk` objects in genuine order.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

# Test-friendly defaults so `get_settings()` is happy. Set BEFORE
# importing `app.*` so the cached Settings sees them. Small batch +
# 4 workers so a modest prose doc yields several batches and the
# sliding window is actually exercised.
os.environ.setdefault("ATHENA_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ATHENA_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ATHENA_OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("ATHENA_JWT_SECRET", "test-secret-32-bytes-or-more-please!")
os.environ.setdefault(
    "ATHENA_STORAGE_DIR", str(Path(tempfile.gettempdir()) / f"athena-test-{os.getpid()}")
)
os.environ.setdefault("ATHENA_INGEST_EMBED_BATCH_SIZE", "4")
os.environ.setdefault("ATHENA_INGEST_EMBED_WORKERS", "4")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ingestion import pipeline  # noqa: E402
from app.services.ingestion import chunker  # noqa: E402
from app.services.ingestion.extractors import extract as extract_text  # noqa: E402


# Per-call records from the stubbed encoder: (start, end, thread_id).
_embed_intervals: list[tuple[float, float, int]] = []


def _fake_encode_one_batch(batch, normalize):
    """Deterministic stand-in for the MiniLM forward pass.

    Sleeps briefly so concurrent calls overlap in wall-clock — this
    models the GIL-released matmul, which is the whole reason the
    thread fan-out is safe and faster. Records (start, end, tid) so
    the test can assert real concurrency.
    """
    from app.services.embedding import settings as _s

    tid = threading.get_ident()
    t0 = time.monotonic()
    time.sleep(0.05)
    t1 = time.monotonic()
    _embed_intervals.append((t0, t1, tid))
    dim = _s.EMBED_DIM
    arr = np.arange(len(batch) * dim, dtype=np.float32).reshape(len(batch), dim)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
    return arr


async def _noop(*args, **kwargs):
    return None


class _StubSession:
    """AsyncSession stand-in: the stubbed store helpers never touch it,
    so it only needs commit/rollback."""

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _max_concurrent(intervals: list[tuple[float, float, int]]) -> int:
    """Sweep-line max overlap of [start, end) intervals."""
    if not intervals:
        return 0
    events: list[tuple[float, int]] = []
    for s, e, _ in intervals:
        events.append((s, 1))
        events.append((e, -1))
    events.sort(key=lambda x: (x[0], -x[1]))  # ends before starts at same t
    cur = 0
    best = 0
    for _, delta in events:
        cur += delta
        best = max(best, cur)
    return best


def _prose_body(paragraphs: int) -> str:
    return (
        "Athena is a personal research assistant. It indexes your "
        "documents and answers questions grounded in them. The pipeline "
        "splits long text into overlapping chunks, embeds each chunk "
        "with a sentence-transformer model, and stores the vectors in "
        "Postgres via pgvector. Retrieval is hybrid: lexical (BM25 over "
        "tsvector) plus vector (HNSW cosine), fused with reciprocal rank. "
    ) * max(1, paragraphs)


@pytest.mark.asyncio
async def test_parallel_embed_preserves_order_and_runs_concurrently(tmp_path):
    """End-to-end drive of the parallel embed loop with stubs."""
    _embed_intervals.clear()

    # Build a prose doc big enough to yield several batches at
    # batch_size=4 (~6k tokens => ~20 chunks => ~5 batches).
    body = _prose_body(60)
    file_path = tmp_path / "doc.txt"
    file_path.write_text(body, encoding="utf-8")

    # Reference: the exact chunks the chunker emits, in order.
    result = await pipeline.asyncio.to_thread(extract_text, file_path)
    reference_contents = [c.content for c in chunker.iter_chunks(result)]
    chunk_count = len(reference_contents)
    assert chunk_count > 0

    copied_batches: list[list[str]] = []

    async def fake_copy_chunks(session, *, document_id, user_id, chunks, embeddings, keywords):
        copied_batches.append([c.content for c in chunks])
        # Sanity: embeddings row count matches chunk count for this batch.
        assert len(embeddings) == len(chunks)

    document = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        started_at=None,
        status="uploaded",
    )
    session = _StubSession()

    with patch.object(pipeline, "_encode_one_batch", _fake_encode_one_batch), \
         patch.object(pipeline, "candidate_phrases", lambda _text: []), \
         patch.object(pipeline, "set_rls_user", _noop), \
         patch.object(pipeline.store, "delete_existing_chunks", _noop), \
         patch.object(pipeline.store, "mark_document_status", _noop), \
         patch.object(pipeline.store, "mark_document_progress", _noop), \
         patch.object(pipeline.store, "finalize_indexing", _noop), \
         patch.object(pipeline.store, "copy_chunks", fake_copy_chunks):
        n = await pipeline.ingest_document(
            session,
            document=document,
            file_path=file_path,
            status_cb=pipeline._noop_status,
        )

    # 1. Returned chunk count matches the chunker's output.
    assert n == chunk_count

    # 2. Every chunk embedded exactly once (one embed call per batch).
    batch_size = pipeline.settings.INGEST_EMBED_BATCH_SIZE
    expected_batches = (chunk_count + batch_size - 1) // batch_size
    assert len(_embed_intervals) == expected_batches

    # 3. Every chunk persisted exactly once, in order.
    flattened = [c for batch in copied_batches for c in batch]
    assert flattened == reference_contents

    # 4. The embed forward-passes actually overlapped (workers=4).
    # With strictly sequential embeds this would be 1; the sliding
    # window should put several in flight at once.
    assert _max_concurrent(_embed_intervals) >= 2


@pytest.mark.asyncio
async def test_parallel_embed_single_worker_is_sequential(tmp_path):
    """With workers=1 the window holds one batch, so embeds must NOT
    overlap — guards against accidentally unbounded fan-out."""
    _embed_intervals.clear()
    body = _prose_body(60)
    file_path = tmp_path / "doc.txt"
    file_path.write_text(body, encoding="utf-8")

    copied_batches: list[list[str]] = []

    async def fake_copy_chunks(session, *, document_id, user_id, chunks, embeddings, keywords):
        copied_batches.append([c.content for c in chunks])

    document = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), started_at=None, status="uploaded"
    )
    session = _StubSession()

    with patch.object(pipeline.settings, "ingest_embed_workers", 1), \
         patch.object(pipeline, "_encode_one_batch", _fake_encode_one_batch), \
         patch.object(pipeline, "candidate_phrases", lambda _text: []), \
         patch.object(pipeline, "set_rls_user", _noop), \
         patch.object(pipeline.store, "delete_existing_chunks", _noop), \
         patch.object(pipeline.store, "mark_document_status", _noop), \
         patch.object(pipeline.store, "mark_document_progress", _noop), \
         patch.object(pipeline.store, "finalize_indexing", _noop), \
         patch.object(pipeline.store, "copy_chunks", fake_copy_chunks):
        await pipeline.ingest_document(
            session, document=document, file_path=file_path, status_cb=pipeline._noop_status
        )

    # Sequential: at most one embed in flight at any instant.
    assert _max_concurrent(_embed_intervals) == 1
    # Still embedded+stored every chunk exactly once.
    result = await pipeline.asyncio.to_thread(extract_text, file_path)
    chunk_count = len(list(chunker.iter_chunks(result)))
    assert sum(len(b) for b in copied_batches) == chunk_count
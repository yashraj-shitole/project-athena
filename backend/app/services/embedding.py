"""Embedding model singleton + helpers.

The model is loaded once per process. Reused by:
- ingestion: chunk embedding
- retrieval: query embedding
- keyword extraction: chunk embedding for cosine-based selection

`SentenceTransformer` is imported lazily so test environments without
torch (or even without sentence-transformers installed) can still
import this module — `get_model()` is what actually needs the heavy
imports, and only at first use.
"""
from __future__ import annotations

import asyncio
import os
import threading
from functools import lru_cache
from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional, Sequence

import numpy as np

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from sentence_transformers import SentenceTransformer

log = get_logger(__name__)

_lock = threading.Lock()
_model: "SentenceTransformer | None" = None

# Whether we've already applied the torch intra-op thread cap for
# parallel encoding. `torch.set_num_threads` is process-global and
# applies to all subsequent torch ops, so we only want to pay the
# (tiny) cost and the side-effect once.
_torch_threads_tuned = False


def resolve_embed_workers() -> int:
    """Resolve the ingestion embedding worker count.

    ``settings.INGEST_EMBED_WORKERS`` is the raw knob; ``0`` means
    auto-pick (min(8, cpu_count)). The result is clamped to >=1.
    """
    raw = settings.INGEST_EMBED_WORKERS
    if raw and raw > 0:
        return raw
    cpu = os.cpu_count() or 4
    return max(1, min(8, cpu))


def tune_torch_threads_for_workers(workers: int) -> None:
    """Cap torch's intra-op thread pool so fanning `workers` concurrent
    encodes across threads doesn't oversubscribe the CPU.

    With the default torch thread count (= cpu_count), running N
    concurrent ``model.encode`` calls spawns N×cpu threads that thrash
    the scheduler and lose the parallel speedup. We cap intra-op
    threads to ``cpu // workers`` (>=1) so the total in-flight threads
    stays ≈ cpu_count. This is process-global and one-shot; it also
    applies to retrieval query encoding afterwards, which is a single
    sentence and barely affected.

    No-op when workers <= 1 (preserve the single-batch, full-thread
    path) or when torch isn't importable (test envs stub the model).
    """
    global _torch_threads_tuned
    if _torch_threads_tuned or workers <= 1:
        return
    try:
        import torch

        cpu = os.cpu_count() or workers
        torch.set_num_threads(max(1, cpu // workers))
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding.torch_threads.tune_failed", error=str(exc))
    _torch_threads_tuned = True


def get_model() -> "SentenceTransformer":
    """Lazy, thread-safe model singleton."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                # Defer the heavy import to first use so test suites
                # that never embed anything don't pay the cost (and
                # don't crash on environments without torch).
                from sentence_transformers import SentenceTransformer

                log.info(
                    "embedding.model.load",
                    model=settings.EMBED_MODEL_NAME,
                    dim=settings.EMBED_DIM,
                )
                _model = SentenceTransformer(settings.EMBED_MODEL_NAME)
    return _model


def encode(texts: Sequence[str], normalize: bool = True) -> np.ndarray:
    """Encode `texts` into a (N, dim) float32 numpy array.

    Always L2-normalized when `normalize=True` so cosine == dot product
    (which is what `<=>` does in pgvector with `vector_cosine_ops`).
    """
    if not texts:
        return np.zeros((0, settings.EMBED_DIM), dtype=np.float32)
    model = get_model()
    vecs = model.encode(
        list(texts),
        batch_size=32,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=normalize,
    )
    return vecs.astype(np.float32, copy=False)


# Default batch size for the streaming variant. Matches `encode()`'s
# implicit value so callers see the same memory profile.
_EMBED_BATCH_SIZE = 32


def _encode_one_batch(
    batch: list[str], normalize: bool
) -> np.ndarray:
    """Encode a single batch on the sync model. Runs in a worker thread."""
    model = get_model()
    vecs = model.encode(
        batch,
        batch_size=len(batch) or 1,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=normalize,
    )
    return vecs.astype(np.float32, copy=False)


async def encode_batched(
    texts: Sequence[str],
    batch_size: int = _EMBED_BATCH_SIZE,
    normalize: bool = True,
    on_batch: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> np.ndarray:
    """Encode `texts` in batches, reporting progress between each.

    Mirrors `encode()` (L2-normalization, float32 output) but iterates
    batches explicitly so the caller can stream progress to the UI
    (SSE for document ingestion). `on_batch(completed, total)` is
    awaited after each successful batch — making the outer function
    async is the cleanest way to let the event loop run pending SSE
    writes between encodes; the encode itself runs in a worker thread
    to keep the loop free.

    `completed` is the number of batches finished (1-indexed); `total`
    is the total number of batches. The final await fires after the
    last batch with `completed == total`.
    """
    if not texts:
        return np.zeros((0, settings.EMBED_DIM), dtype=np.float32)
    items = list(texts)
    total = (len(items) + batch_size - 1) // batch_size
    out = np.zeros((len(items), settings.EMBED_DIM), dtype=np.float32)
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        # `to_thread` keeps the event loop responsive while the CPU-
        # bound encoder runs. Without this, an SSE write pending in
        # the loop wouldn't be flushed until the whole encode returned.
        vecs = await asyncio.to_thread(_encode_one_batch, batch, normalize)
        out[i : i + len(batch)] = vecs
        completed = (i // batch_size) + 1
        if on_batch is not None:
            await on_batch(completed, total)
    return out


def encode_one(text: str, normalize: bool = True) -> List[float]:
    """Encode a single string to a python list (for JSON / SQL params)."""
    vec = encode([text], normalize=normalize)
    if vec.size == 0:
        return [0.0] * settings.EMBED_DIM
    return vec[0].tolist()

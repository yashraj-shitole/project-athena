"""Embedding model singleton + helpers.

The model is loaded once per process. Reused by:
- ingestion: chunk embedding
- retrieval: query embedding
- keyword extraction: chunk embedding for cosine-based selection
"""
from __future__ import annotations

import asyncio
import threading
from functools import lru_cache
from typing import Awaitable, Callable, List, Optional, Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Lazy, thread-safe model singleton."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
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

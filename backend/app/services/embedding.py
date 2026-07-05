"""Embedding model singleton + helpers.

The model is loaded once per process. Reused by:
- ingestion: chunk embedding
- retrieval: query embedding
- keyword extraction: chunk embedding for cosine-based selection
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import List, Sequence

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


def encode_one(text: str, normalize: bool = True) -> List[float]:
    """Encode a single string to a python list (for JSON / SQL params)."""
    vec = encode([text], normalize=normalize)
    if vec.size == 0:
        return [0.0] * settings.EMBED_DIM
    return vec[0].tolist()

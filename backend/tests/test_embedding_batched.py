"""Unit tests for the streaming embed helper.

We don't need a real model here — the test mocks the singleton and
asserts that `on_batch(current, total)` is awaited exactly once per
batch, with `completed` reaching `total` on the final call.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fake_encode(items, **kwargs):
    """Drop-in stand-in for SentenceTransformer.encode that returns
    deterministic vectors of the configured embed dim."""
    from app.services.embedding import settings as _settings

    dim = _settings.EMBED_DIM
    out = np.arange(len(items) * dim, dtype=np.float32).reshape(len(items), dim)
    out /= np.linalg.norm(out, axis=1, keepdims=True) + 1e-9
    return out


class _FakeModel:
    def encode(self, items, **kwargs):
        return _fake_encode(items, **kwargs)


@pytest.mark.asyncio
async def test_encode_batched_calls_on_batch_per_batch():
    """A 10-item text list with batch_size=4 should fire on_batch 3 times."""
    from app.services import embedding

    texts = [f"item {i}" for i in range(10)]
    seen: list[tuple[int, int]] = []

    async def on_batch(current, total):
        seen.append((current, total))

    with patch.object(embedding, "get_model", return_value=_FakeModel()):
        out = await embedding.encode_batched(
            texts, batch_size=4, on_batch=on_batch
        )

    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert out.shape == (10, embedding.settings.EMBED_DIM)


@pytest.mark.asyncio
async def test_encode_batched_no_callback_still_works():
    """`on_batch=None` is the default for callers that don't need progress."""
    from app.services import embedding

    texts = [f"item {i}" for i in range(3)]
    with patch.object(embedding, "get_model", return_value=_FakeModel()):
        out = await embedding.encode_batched(texts, batch_size=2)
    assert out.shape == (3, embedding.settings.EMBED_DIM)


@pytest.mark.asyncio
async def test_encode_batched_empty_returns_zero_row():
    """No text in → no batches, no callbacks, empty result."""
    from app.services import embedding

    async def on_batch(current, total):
        raise AssertionError("should not be called for empty input")

    with patch.object(embedding, "get_model", return_value=_FakeModel()):
        out = await embedding.encode_batched([], on_batch=on_batch)
    assert out.shape == (0, embedding.settings.EMBED_DIM)

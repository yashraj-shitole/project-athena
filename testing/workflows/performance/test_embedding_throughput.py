"""Performance: embedding throughput.

Measures the local sentence-transformer model throughput on a
batch of 100 synthetic texts. The default budget is 5 seconds
for 100 short sentences on CPU; override with --embedding-budget.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.perf, pytest.mark.slow]


def pytest_addoption(parser):
    parser.addoption(
        "--embedding-budget",
        action="store",
        type=float,
        default=5.0,
        help="Embedding throughput budget for 100 texts in seconds (default: 5).",
    )


def test_embedding_encode_batched(request):
    pytest.importorskip("sentence_transformers")
    from sentence_transformers import SentenceTransformer
    budget = request.config.getoption("--embedding-budget")

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    texts = [f"This is test sentence number {i}." for i in range(100)]
    import time
    t0 = time.perf_counter()
    embs = model.encode(texts, batch_size=32, show_progress_bar=False)
    dt = time.perf_counter() - t0
    n = len(embs)
    print(f"\nembedding throughput: {n} texts in {dt:.2f}s ({n/dt:.1f} texts/s)")
    assert embs.shape[0] == 100
    assert embs.shape[1] == 384
    assert dt < budget, f"embedding throughput {dt:.2f}s exceeds budget {budget}s"


def test_embedding_single_text_latency():
    pytest.importorskip("sentence_transformers")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    import time
    samples = []
    for i in range(20):
        t0 = time.perf_counter()
        model.encode([f"latency test {i}"], show_progress_bar=False)
        samples.append(time.perf_counter() - t0)
    samples.sort()
    p50 = samples[len(samples) // 2]
    p95 = samples[int(0.95 * len(samples)) - 1]
    print(f"\nsingle-text encoding: p50={p50*1000:.0f}ms p95={p95*1000:.0f}ms")
    assert p95 < 0.5

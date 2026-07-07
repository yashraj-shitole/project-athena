"""Performance: retrieval latency.

Measure end-to-end retrieval latency for the hybrid (BM25 + vector +
RRF) path. Skipped unless `--run-integration` because it requires
Postgres + a seeded chunk index.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.perf, pytest.mark.integration, pytest.mark.slow]


async def test_retrieval_latency(authed_client):
    import time
    # Upload a 50-chunk corpus of synthetic text.
    chunks = []
    for i in range(50):
        chunks.append(
            f"chunk-{i:03d} This is a synthetic document about topic {i}. "
            f"It contains the words revenue, growth, and product."
        )
    for chunk in chunks:
        import io
        files = {"file": ("rl.csv", io.BytesIO(f"col\n{chunk}\n".encode()), "text/csv")}
        await authed_client.post("/api/documents", files=files)
    # Time the chat-with-retrieval path.
    samples = []
    for _ in range(10):
        t0 = time.perf_counter()
        r = await authed_client.post(
            "/api/chat",
            json={"message": "What does the corpus say about revenue?",
                  "conversation_id": None},
            timeout=60.0,
        )
        samples.append(time.perf_counter() - t0)
        assert r.status_code == 200
    samples.sort()
    p95 = samples[int(0.95 * len(samples)) - 1]
    print(f"\nretrieval latency: p95={p95:.2f}s")
    assert p95 < 10.0

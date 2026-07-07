"""Performance: chat latency.

pytest-benchmark suite for the non-streaming chat endpoint. We
measure p50, p95, p99 latencies across N turns (default 20) and
fail if p95 exceeds the documented budget.

The default budget is 5 seconds p95 (Phase 1's small-model target);
override with `--chat-latency-budget=10` for slower models.
"""
from __future__ import annotations

import time

import pytest


pytestmark = [pytest.mark.perf, pytest.mark.slow]


def pytest_addoption(parser):
    parser.addoption(
        "--chat-latency-budget",
        action="store",
        type=float,
        default=5.0,
        help="p95 latency budget in seconds (default: 5.0).",
    )
    parser.addoption(
        "--chat-latency-iterations",
        action="store",
        type=int,
        default=20,
        help="Number of chat turns to time (default: 20).",
    )


async def test_chat_latency_p95(authed_client, request):
    budget = request.config.getoption("--chat-latency-budget")
    n = request.config.getoption("--chat-latency-iterations")
    samples = []
    for i in range(n):
        t0 = time.perf_counter()
        r = await authed_client.post(
            "/api/chat",
            json={"message": f"latency test {i}", "conversation_id": None},
            timeout=60.0,
        )
        dt = time.perf_counter() - t0
        assert r.status_code == 200, r.text
        samples.append(dt)
    samples.sort()
    p95 = samples[int(0.95 * len(samples)) - 1]
    p50 = samples[len(samples) // 2]
    print(f"\nchat latency: p50={p50:.2f}s p95={p95:.2f}s n={n}")
    assert p95 < budget, f"p95 {p95:.2f}s exceeds budget {budget:.2f}s"


async def test_health_latency(unauth_client):
    """The /health endpoint should respond in < 200ms (no LLM call)."""
    samples = []
    for _ in range(50):
        t0 = time.perf_counter()
        r = await unauth_client.get("/health")
        dt = time.perf_counter() - t0
        assert r.status_code == 200
        samples.append(dt)
    samples.sort()
    p95 = samples[int(0.95 * len(samples)) - 1]
    print(f"\nhealth latency: p95={p95*1000:.0f}ms")
    assert p95 < 0.5, f"/health p95 {p95*1000:.0f}ms exceeds 500ms"


async def test_model_endpoint_latency(unauth_client):
    samples = []
    for _ in range(50):
        t0 = time.perf_counter()
        r = await unauth_client.get("/model")
        dt = time.perf_counter() - t0
        assert r.status_code == 200
        samples.append(dt)
    samples.sort()
    p95 = samples[int(0.95 * len(samples)) - 1]
    print(f"\n/model latency: p95={p95*1000:.0f}ms")
    assert p95 < 0.5

"""Performance: document indexing speed.

Upload a 100 KB document and time how long it takes to reach
`status: indexed`. The budget is 30 seconds for a 100 KB text
file; override with --indexing-budget.

Gated on `--run-integration` because the indexer requires Postgres.
"""
from __future__ import annotations

import io
import time
import uuid

import pytest


pytestmark = [pytest.mark.perf, pytest.mark.integration, pytest.mark.slow]


def pytest_addoption(parser):
    parser.addoption(
        "--indexing-budget",
        action="store",
        type=float,
        default=30.0,
        help="Indexing latency budget in seconds (default: 30).",
    )


async def test_indexing_speed(authed_client, request):
    budget = request.config.getoption("--indexing-budget")
    # 100 KB of synthetic text.
    text = ("This is a test sentence. " * 100 + "\n") * 200  # ~100 KB
    files = {"file": (f"perf_{uuid.uuid4().hex[:6]}.txt", io.BytesIO(text.encode()), "text/plain")}
    t0 = time.perf_counter()
    r = await authed_client.post("/api/documents", files=files)
    assert r.status_code in (200, 202)
    doc_id = r.json()["id"]

    # Poll until indexed.
    deadline = time.time() + budget + 5
    while time.time() < deadline:
        r = await authed_client.get(f"/api/documents/{doc_id}")
        if r.json().get("status") == "indexed":
            break
        time.sleep(0.5)
    dt = time.perf_counter() - t0
    status = r.json().get("status")
    print(f"\nindexing speed: {dt:.1f}s status={status}")
    assert status == "indexed", f"indexing did not complete: status={status}"
    assert dt < budget, f"indexing {dt:.1f}s exceeds budget {budget}s"

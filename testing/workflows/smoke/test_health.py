"""Smoke tests for the /health, /model, /metrics endpoints.

These run against a live api container (the dev stack started by
`docker-up.ps1`). They assert the response shape, not the values
(which are environment-dependent).
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.smoke


async def test_health_returns_ok_envelope(unauth_client):
    r = await unauth_client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "checks" in data
    checks = data["checks"]
    # db/redis/llm are the three sub-checks the orchestrator runs.
    for key in ("db", "redis", "llm"):
        assert key in checks, f"missing health check: {key}"
        assert "ok" in checks[key]
        assert "ms" in checks[key]


async def test_health_does_not_require_auth(unauth_client):
    r = await unauth_client.get("/health")
    assert r.status_code == 200
    assert "Authorization" not in r.request.headers


async def test_model_returns_active_config(unauth_client):
    r = await unauth_client.get("/model")
    assert r.status_code == 200
    data = r.json()
    # Required keys per docs/api.md.
    for key in ("model", "provider", "base_url", "context_budget", "embedding_model", "embedding_dim"):
        assert key in data, f"missing model field: {key}"
    assert isinstance(data["context_budget"], int) and data["context_budget"] > 0
    assert isinstance(data["embedding_dim"], int) and data["embedding_dim"] > 0


async def test_metrics_returns_cache_counters(unauth_client):
    r = await unauth_client.get("/metrics")
    assert r.status_code == 200
    data = r.json()
    # The endpoint may return a dict (Prometheus-shaped) or a list —
    # be lenient; only assert that *some* counters are present.
    if isinstance(data, dict):
        # At least one of the standard counters is exposed.
        assert any(
            k in data for k in ("cache_hits", "cache_misses", "cache_hit_rate")
        )
    else:
        assert isinstance(data, list) and len(data) >= 1


async def test_docs_serves_openapi(unauth_client):
    r = await unauth_client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "openapi" in spec
    assert "paths" in spec
    # Spot-check that the major route groups are present.
    for prefix in ("/api/auth", "/api/documents", "/api/chat", "/health", "/model"):
        assert any(p.startswith(prefix) for p in spec["paths"]), (
            f"no paths under {prefix}"
        )

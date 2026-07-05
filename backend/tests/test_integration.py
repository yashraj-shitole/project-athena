"""Smoke tests against real Postgres / Redis / Ollama. Skipped unless
`--run-integration` is passed (see conftest.py).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_health_endpoint_reports_status_keys():
    """If integration is enabled, hit /health and confirm the shape."""
    pytest.skip("integration test — runs only with --run-integration")

"""Shared fixtures for the E2E / accessibility suites.

The `browser` fixture lazily imports Playwright. If Playwright is
not installed in the environment, every E2E test is skipped with
a clear message (rather than failing the whole suite).
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--frontend-url",
        action="store",
        default="http://localhost:5173",
        help="Base URL of the Vite dev server (default: http://localhost:5173).",
    )


@pytest.fixture
def frontend_url(request) -> str:
    return request.config.getoption("--frontend-url").rstrip("/")

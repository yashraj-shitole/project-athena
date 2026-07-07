"""Pytest fixtures. Tests that need Postgres/Redis are skipped by default
to keep the unit suite hermetic. Run with `--run-integration` to opt in.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make `app` importable when pytest is invoked from any CWD.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Generate a real Fernet key BEFORE `get_settings()` is first called, so
# the cached Settings instance sees a valid key on first import. The
# placeholder below is a 32-zero-byte urlsafe-b64 (Fernet accepts it
# but it's the "obvious" key — fine for tests).
from cryptography.fernet import Fernet  # noqa: E402
_REAL_FERNET_KEY = Fernet.generate_key().decode()
os.environ["ATHENA_CONNECTOR_KEY"] = _REAL_FERNET_KEY

# Force a test-friendly settings override (no DB connection on import).
os.environ.setdefault("ATHENA_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ATHENA_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ATHENA_OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("ATHENA_JWT_SECRET", "test-secret-32-bytes-or-more-please!")
# Use a per-process temp storage dir so we never touch the real one.
os.environ.setdefault(
    "ATHENA_STORAGE_DIR", str(Path("/tmp") / f"athena-test-{os.getpid()}")
)

import pytest  # noqa: E402


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run tests that require Postgres/Redis/Ollama.",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration", default=False):
        skip = pytest.mark.skip(reason="integration test (--run-integration)")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

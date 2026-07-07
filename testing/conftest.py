"""Shared pytest fixtures for the /testing/ tree.

Re-uses the env-var scaffolding from `backend/tests/conftest.py`:
- Forces hermetic in-memory SQLite for the unit-ish suites.
- Forces a real Fernet key before any `app.*` import.
- Forces a temp `ATHENA_STORAGE_DIR` so we never touch the real one.

The `--run-integration` flag is the same gate `backend/tests/conftest.py`
already enforces. We re-register it here so `pytest -m integration` from
inside `testing/` works without importing the backend conftest as a
plugin (which would pull in the entire backend dependency tree).

Layers that don't need Postgres/Redis/Ollama (smoke, regression, e2e,
security, a11y) can still run against a live api container — they hit
the HTTP surface, not the DB. The integration marker is reserved for
tests that genuinely need SQLAlchemy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `app` importable when pytest is invoked from inside `testing/`.
_TESTING_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _TESTING_ROOT.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
for p in (_REPO_ROOT, _BACKEND_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

# Generate a real Fernet key BEFORE `get_settings()` is first called.
from cryptography.fernet import Fernet  # noqa: E402
_REAL_FERNET_KEY = Fernet.generate_key().decode()
os.environ["ATHENA_CONNECTOR_KEY"] = _REAL_FERNET_KEY

# Force a test-friendly settings override (no DB connection on import).
os.environ.setdefault("ATHENA_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ATHENA_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ATHENA_OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("ATHENA_JWT_SECRET", "test-secret-32-bytes-or-more-please!")
# Per-process temp storage dir.
os.environ.setdefault(
    "ATHENA_STORAGE_DIR", str(Path("/tmp") / f"athena-testing-{os.getpid()}")
)
# Test CORS allows the dev frontend.
os.environ.setdefault(
    "ATHENA_CORS_ORIGINS", '["http://localhost:5173","http://localhost:8080"]'
)

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Pytest options
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    # Re-declared so `pytest -m integration` from inside testing/ works
    # even if backend/conftest.py is not auto-loaded as a plugin.
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run tests that require Postgres/Redis/Ollama.",
    )
    parser.addoption(
        "--base-url",
        action="store",
        default=os.environ.get("ATHENA_TEST_BASE_URL", "http://localhost:8000"),
        help="Base URL of the running api container (default: $ATHENA_TEST_BASE_URL or http://localhost:8000).",
    )
    parser.addoption(
        "--llm-judge",
        action="store",
        choices=["ollama", "openai", "heuristic"],
        default=os.environ.get("ATHENA_EVAL_JUDGE", "ollama"),
        help="Which LLM judge to use for eval scenarios (default: $ATHENA_EVAL_JUDGE or ollama).",
    )


def pytest_collection_modifyitems(config, items):
    """Same skip-on-no-flag behavior as backend/conftest.py."""
    if config.getoption("--run-integration", default=False):
        return
    skip = pytest.mark.skip(reason="integration test (--run-integration)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------------------
# HTTP client (smoke / regression / e2e)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url(request) -> str:
    return request.config.getoption("--base-url").rstrip("/")


@pytest.fixture
def http_client():
    """Plain httpx.AsyncClient — no auth. Use for /health, /model, /metrics."""
    import httpx

    return httpx.AsyncClient(timeout=15.0)


@pytest.fixture
async def unauth_client(base_url):
    import httpx

    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as c:
        yield c


# ---------------------------------------------------------------------------
# Auth fixtures (register, login, JWT pair)
# ---------------------------------------------------------------------------

@pytest.fixture
async def registered_user(unauth_client):
    """Register a fresh user with a unique email; return the UserPublic dict."""
    import uuid

    email = f"pytest+{uuid.uuid4().hex[:8]}@example.com"
    password = "Testpass!234"
    resp = await unauth_client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": "pytest"},
    )
    resp.raise_for_status()
    user = resp.json()
    user["_password"] = password
    return user


@pytest.fixture
async def auth_pair(unauth_client, registered_user):
    """Return a (user_dict, headers_with_bearer) tuple."""
    resp = await unauth_client.post(
        "/api/auth/login-json",
        json={"email": registered_user["email"], "password": registered_user["_password"]},
    )
    resp.raise_for_status()
    tokens = resp.json()
    return registered_user, {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
async def authed_client(base_url, auth_pair):
    """AsyncClient with the Authorization header pre-set."""
    import httpx

    _user, headers = auth_pair
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as c:
        yield c


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def eval_judge(request):
    """Build a JudgeAdapter per the `--llm-judge` flag.

    Lazy import — the eval package may not be importable in every env.
    """
    from llm_evals.eval.judges import get_judge

    return get_judge(choice=request.config.getoption("--llm-judge"))


@pytest.fixture
def eval_runner(eval_judge):
    """Return a callable that runs a single scenario end-to-end.

    The runner writes a JSON record into `llm-evals/reports/<run-id>.jsonl`
    so post-run reports can be generated without re-running the suite.
    """
    from llm_evals.eval.runners import EvalRunner

    return EvalRunner(judge=eval_judge)

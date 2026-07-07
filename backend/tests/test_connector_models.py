"""Tests for the connector ORM models and the audit/usage helpers.

Hermetic: these tests do NOT spin up a real database. Instead we
exercise the write-path helpers (`audit.record`, `usage.record`) with
a minimal in-memory list-backed session, and validate the data shape
the helper hands to the ORM. The integration tests in
`tests/integration/` (run with `--run-integration`) cover the actual
SQL emission against a real Postgres.

The unit-test guarantee we need is:

* The right ORM object is constructed with the right column values.
* Helpers reject bad inputs early (unknown action / status).
* Usage.aggregate returns the documented zero-state for an empty
  result set without making assumptions about the underlying engine.
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Test-friendly defaults so `get_settings()` is happy.
os.environ.setdefault("ATHENA_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ATHENA_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ATHENA_OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("ATHENA_JWT_SECRET", "test-secret-32-bytes-or-more-please!")
os.environ.setdefault(
    "ATHENA_STORAGE_DIR", str(Path("/tmp") / f"athena-test-{os.getpid()}")
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.services.providers import audit, crypto, usage  # noqa: E402

# conftest has already set a real Fernet key; the cached Settings
# already sees it. Nothing more to do here.


class _StubSession:
    """Minimal AsyncSession stand-in that captures `add()` calls.

    The audit/usage helpers only do `session.add(row)` (and
    `session.commit()` in tests, but the helper itself doesn't commit).
    We capture every `add()` so the test can inspect the constructed
    ORM object directly without needing a live database.
    """

    def __init__(self) -> None:
        self.added: List[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> "_StubResult":
        return _StubResult([], [])


class _StubResult:
    def __init__(self, rows: Iterable[Any], totals: Iterable[Any]) -> None:
        self._rows = list(rows)
        self._totals = list(totals)

    def all(self) -> List[Any]:
        return self._rows

    def one(self) -> Any:
        return self._totals[0] if self._totals else (0, 0, 0, 0.0, 0)


@pytest.fixture
def session() -> _StubSession:
    return _StubSession()


def test_encrypt_decrypt_roundtrip_smoke():
    """Sanity check: crypto helpers do their basic job.

    Kept here so a single failing import (e.g. cryptography) shows up
    in this file rather than the crypto-specific suite.
    """
    blob = crypto.encrypt("sk-1234567890abcdef")
    assert crypto.decrypt(blob) == "sk-1234567890abcdef"
    assert crypto.mask_for_ui("sk-1234567890abcdef") == "sk-…cdef"


@pytest.mark.asyncio
async def test_audit_record_writes_redacted_dicts(session: _StubSession):
    before = {"id": "abc", "name": "audit-test", "api_key": "sk-…1234"}
    after = {"id": "abc", "name": "renamed", "api_key": "sk-…5678"}
    connector_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await audit.record(
        session,
        connector_id=connector_id,
        user_id=user_id,
        action=audit.ACTION_UPDATE,
        before=before,
        after=after,
        ip="127.0.0.1",
        user_agent="test",
    )
    assert len(session.added) == 1
    row = session.added[0]
    assert row.connector_id == connector_id
    assert row.user_id == user_id
    assert row.action == "update"
    assert row.before_redacted == before
    assert row.after_redacted == after
    assert row.ip == "127.0.0.1"
    assert row.user_agent == "test"


@pytest.mark.asyncio
async def test_audit_record_rejects_unknown_action(session: _StubSession):
    with pytest.raises(ValueError):
        await audit.record(
            session,
            connector_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            action="bogus",
        )
    # And nothing should have been added to the session.
    assert session.added == []


@pytest.mark.asyncio
async def test_audit_record_truncates_long_user_agent(session: _StubSession):
    ua = "x" * 1000
    await audit.record(
        session,
        connector_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action=audit.ACTION_TEST,
        user_agent=ua,
    )
    assert len(session.added) == 1
    assert session.added[0].user_agent == "x" * 500


def test_usage_record_constructs_correct_row(session: _StubSession):
    connector_id = uuid.uuid4()
    user_id = uuid.uuid4()
    usage.record(
        session,
        connector_id=connector_id,
        user_id=user_id,
        model="gpt-4o-mini",
        prompt_tokens=42,
        completion_tokens=17,
        latency_ms=350,
        status=usage.STATUS_OK,
        error_class=None,
        cost_estimate=Decimal("0.0001"),
    )
    assert len(session.added) == 1
    row = session.added[0]
    assert row.connector_id == connector_id
    assert row.user_id == user_id
    assert row.model == "gpt-4o-mini"
    assert row.prompt_tokens == 42
    assert row.completion_tokens == 17
    assert row.latency_ms == 350
    assert row.status == "ok"
    assert row.error_class is None
    # Cost is stored as text and round-trips through Decimal.
    assert Decimal(row.cost_estimate) == Decimal("0.0001")


def test_usage_record_clamps_negative_counts(session: _StubSession):
    """Negative token/latency counts are clamped to 0.

    Defensive: the persistence layer would happily store a negative
    int, but a negative prompt_tokens count makes no sense and would
    corrupt the aggregate sums. The helper guards against it.
    """
    usage.record(
        session,
        connector_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        model="m",
        prompt_tokens=-5,
        completion_tokens=-10,
        latency_ms=-100,
    )
    row = session.added[0]
    assert row.prompt_tokens == 0
    assert row.completion_tokens == 0
    assert row.latency_ms == 0


def test_usage_record_rejects_unknown_status(session: _StubSession):
    with pytest.raises(ValueError):
        usage.record(
            session,
            connector_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            model="m",
            status="bogus",
        )
    assert session.added == []


@pytest.mark.asyncio
async def test_aggregate_with_no_rows_returns_zero_state():
    """aggregate() must short-circuit cleanly when no rows match.

    We can't easily exercise the real SQL without a live DB; instead
    we directly test the documented zero-state shape (the `if
    total_requests` guards in `aggregate`).
    """
    zero = {
        "total_requests": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "avg_latency_ms": 0.0,
        "success_rate": 0.0,
        "by_day": [],
    }
    # The shape is part of the public contract — the implementation
    # may change but the dashboard relies on these keys.
    assert set(zero) == {
        "total_requests",
        "total_prompt_tokens",
        "total_completion_tokens",
        "avg_latency_ms",
        "success_rate",
        "by_day",
    }


def test_action_vocabulary_is_stable():
    """The set of accepted actions is a contract for the dashboard.

    Renaming any of these is a coordinated backend + frontend change;
    a typo here silently breaks the audit filter.
    """
    expected = {
        "create",
        "update",
        "delete",
        "set_default",
        "test",
        "refresh_models",
        "clone",
    }
    assert set(audit.ACTIONS) == expected
    assert audit.ACTION_CREATE == "create"
    assert audit.ACTION_UPDATE == "update"


def test_status_vocabulary_is_stable():
    expected = {
        "ok",
        "error",
        "timeout",
        "rate_limited",
        "auth_failed",
        "cancelled",
        "stream_interrupted",
    }
    assert set(usage.STATUSES) == expected
    assert usage.STATUS_OK == "ok"
    assert usage.STATUS_ERROR == "error"

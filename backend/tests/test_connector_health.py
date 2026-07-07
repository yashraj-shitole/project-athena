"""Tests for the background health probe.

The probe walks enabled connectors, calls `provider.health_check()`,
and writes the result back to `model_connectors.last_health*`.
After `connector_health_failure_threshold` consecutive failures,
the connector is auto-disabled.

Tests use a stub `AsyncSession` (same pattern as
`test_connector_models.py`) so we don't need a real DB. The
adapter's `health_check()` is replaced with a fake via a fake
`provider` registry entry — the probe builds adapters through the
registry, so a controlled class gets probed.
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.models.connector import ModelConnector
from app.services.providers import base as pal
from app.services.providers import registry as provider_registry
from app.services.providers.health import HealthProbe


# --- Test doubles --------------------------------------------------------

@dataclass
class _FakeRow:
    """Minimum shape of a `ModelConnector` row the probe touches.

    Mirrors the test double in `test_model_router.py`. Adds
    `consecutive_failures` since the probe is the only writer.
    """
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    provider: str
    base_url: str
    default_model: str
    is_enabled: bool = True
    is_default: bool = False
    is_admin: bool = False
    deleted_at: Any = None
    api_key_enc: Optional[bytes] = None
    auth_type: str = "bearer"
    auth_header_name: Optional[str] = None
    custom_headers: Optional[dict] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    api_version: Optional[str] = None
    models: Optional[List[str]] = None
    settings: Optional[dict] = None
    last_health: Optional[str] = None
    last_health_at: Any = None
    last_health_latency_ms: Optional[int] = None
    consecutive_failures: int = 0


class _StubSession:
    """Stub for `AsyncSession`. Captures `add()` calls (audit) and
    serves a configured list of rows from `execute()`."""

    def __init__(self, rows: List[ModelConnector]) -> None:
        self._rows = list(rows)
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def execute(self, _stmt: Any):
        return _StubResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _StubResult:
    def __init__(self, rows: List[ModelConnector]) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


# --- Health-check controllable fake adapter ----------------------------

class _FakeHealthAdapter(pal.ProviderAdapter):
    """A adapter whose `health_check()` returns whatever the test
    queued. Registered under a private name so the probe picks it
    up via the registry."""

    name = "fake_probe"

    def __init__(self, *, report: pal.HealthReport) -> None:
        self._report = report

    async def chat(self, req):  # pragma: no cover
        raise NotImplementedError

    async def stream(self, req):  # pragma: no cover
        if False:
            yield {}
        return

    async def health_check(self) -> pal.HealthReport:
        return self._report

    async def aclose(self) -> None:
        return None


# --- Helpers ------------------------------------------------------------

def _row(**overrides) -> ModelConnector:
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="probe-target",
        provider="fake_probe",
        base_url="https://example.com",
        default_model="m",
        is_enabled=True,
        is_default=False,
        is_admin=False,
        deleted_at=None,
        auth_type="bearer",
        custom_headers={},
        models=[],
        settings={},
        last_health=None,
        last_health_at=None,
        last_health_latency_ms=None,
        consecutive_failures=0,
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return ModelConnector(**base)


# --- Lifecycle ---------------------------------------------------------

@pytest.mark.asyncio
async def test_start_and_stop_probe_lifecycle():
    """`start()` launches the loop, `stop()` cancels it cleanly."""
    p = HealthProbe()
    p.start()
    assert p._task is not None
    await p.stop()
    assert p._task is None


@pytest.mark.asyncio
async def test_stop_is_idempotent():
    """Calling `stop()` twice doesn't blow up."""
    p = HealthProbe()
    p.start()
    await p.stop()
    await p.stop()  # no-op


# --- tick_once: happy path --------------------------------------------

@pytest.mark.asyncio
async def test_tick_writes_health_snapshot_on_success(monkeypatch):
    """A successful probe updates `last_health`, `last_health_at`,
    `last_health_latency_ms`, and resets `consecutive_failures`."""
    row = _row()
    sess = _StubSession([row])
    # Register a fake adapter under the row's `provider` name.
    monkeypatch.setitem(
        provider_registry._REGISTRY,  # type: ignore[attr-defined]
        "fake_probe",
        lambda **kw: _FakeHealthAdapter(
            report=pal.HealthReport(
                ok=True,
                latency_ms=42,
                status="online",
                capabilities={"chat": True},
            )
        ),
    )
    probe = HealthProbe()
    touched = await probe.tick_once(session=sess)
    assert touched == 1
    assert row.last_health == "online"
    assert row.last_health_at is not None
    assert row.last_health_latency_ms == 42
    assert row.consecutive_failures == 0
    assert row.is_enabled is True


# --- tick_once: failure path -----------------------------------------

@pytest.mark.asyncio
async def test_tick_increments_consecutive_failures_on_failure(monkeypatch):
    row = _row()
    sess = _StubSession([row])
    monkeypatch.setitem(
        provider_registry._REGISTRY,  # type: ignore[attr-defined]
        "fake_probe",
        lambda **kw: _FakeHealthAdapter(
            report=pal.HealthReport(
                ok=False,
                status="offline",
                error="connection refused",
                category=pal.CAT_NETWORK,
            )
        ),
    )
    probe = HealthProbe()
    await probe.tick_once(session=sess)
    assert row.consecutive_failures == 1
    assert row.last_health == "offline"
    assert row.is_enabled is True  # not yet at threshold


# --- Auto-disable at threshold ---------------------------------------

@pytest.mark.asyncio
async def test_tick_auto_disables_at_failure_threshold(monkeypatch):
    """After `connector_health_failure_threshold` consecutive
    failures, the connector is auto-disabled and an audit row
    is written."""
    settings = get_settings()
    threshold = settings.connector_health_failure_threshold
    row = _row(consecutive_failures=threshold - 1)  # next failure trips it
    sess = _StubSession([row])
    monkeypatch.setitem(
        provider_registry._REGISTRY,  # type: ignore[attr-defined]
        "fake_probe",
        lambda **kw: _FakeHealthAdapter(
            report=pal.HealthReport(
                ok=False, status="offline", error="boom", category=pal.CAT_NETWORK
            )
        ),
    )
    probe = HealthProbe()
    await probe.tick_once(session=sess)
    assert row.is_enabled is False
    assert row.consecutive_failures == threshold
    # The auto-disable writes one audit row.
    assert len(sess.added) == 1
    audit_row = sess.added[0]
    assert audit_row.action == "update"  # audit.ACTION_UPDATE
    after = audit_row.after_redacted or {}
    assert after.get("is_enabled") is False


# --- Skip soft-deleted and disabled rows -----------------------------

@pytest.mark.asyncio
async def test_tick_skips_disabled_and_deleted_rows(monkeypatch):
    """The probe only walks rows where `is_enabled = TRUE` and
    `deleted_at IS NULL` — disabled rows are not retried; soft-
    deleted rows are not probed."""
    enabled_row = _row()
    disabled_row = _row(is_enabled=False)
    # We can't actually pass disabled/deleted through the
    # `select().where(...)` filter in the stub — the stub
    # returns whatever rows it's given. So we test the SQL
    # filter by checking the filter clause in the executed
    # statement.

    captured = {}

    class _CaptureSession(_StubSession):
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _StubResult([enabled_row])

        def add(self, obj):  # noqa: D401
            self.added.append(obj)

    sess = _CaptureSession([enabled_row])
    monkeypatch.setitem(
        provider_registry._REGISTRY,  # type: ignore[attr-defined]
        "fake_probe",
        lambda **kw: _FakeHealthAdapter(
            report=pal.HealthReport(ok=True, status="online")
        ),
    )
    probe = HealthProbe()
    await probe.tick_once(session=sess)
    # The captured statement must include both filters — this
    # is a structural assertion on the SQLAlchemy clause.
    sql = str(captured["stmt"]).lower()
    assert "is_enabled" in sql
    assert "deleted_at" in sql


# --- Probe honors per-cycle cap ------------------------------------

@pytest.mark.asyncio
async def test_tick_caps_rows_per_cycle(monkeypatch):
    """`connector_health_max_per_cycle` bounds the work per cycle
    so a freshly-restarted app with 1000 connectors doesn't
    probe them all at once."""
    settings = get_settings()
    # Build more rows than the cap; the test stub doesn't
    # actually filter — the SQL query does. We assert the
    # setting is sane and used.
    rows = [_row() for _ in range(settings.connector_health_max_per_cycle + 5)]
    sess = _StubSession(rows)
    monkeypatch.setitem(
        provider_registry._REGISTRY,  # type: ignore[attr-defined]
        "fake_probe",
        lambda **kw: _FakeHealthAdapter(
            report=pal.HealthReport(ok=True, status="online")
        ),
    )
    # Spy on _probe_one to count.
    from app.services.providers import health as probe_mod

    seen: list[uuid.UUID] = []

    orig = probe_mod.HealthProbe._probe_one

    async def counting(self, session, row):
        seen.append(row.id)
        await orig(self, session, row)

    monkeypatch.setattr(probe_mod.HealthProbe, "_probe_one", counting)
    probe = HealthProbe()
    touched = await probe.tick_once(session=sess)
    # The probe only walked up to the cap.
    assert touched == settings.connector_health_max_per_cycle
    assert len(seen) == settings.connector_health_max_per_cycle

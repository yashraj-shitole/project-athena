"""Background health probe for External Model Connectors.

A single `asyncio` task is launched in the FastAPI `lifespan` and
ticks every `settings.connector_health_interval_s` seconds. On each
tick, it walks the enabled connectors and calls
`provider.health_check()` on each. Results are written back to
`model_connectors.last_health*` and to the audit log. After
`connector_health_failure_threshold` consecutive failures, the
connector is auto-disabled (`is_enabled = False`) and a structlog
warning is emitted.

The per-connector lock prevents two ticks from probing the same
row at the same time. (A long health probe on connector A doesn't
block the probe on connector B; it just serializes within A.)
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.connector import ModelConnector
from app.services.providers import audit, crypto
from app.services.providers import registry as provider_registry
from app.services.providers.base import ProviderAdapter, ProviderError

log = get_logger(__name__)
_settings = get_settings()


class HealthProbe:
    """The background probe loop + per-connector lock map.

    Lifecycle:

    * `start()` — launches the loop as a background task.
    * `stop()` — cancels the loop, awaits it, releases adapters.
    * `_tick()` — one iteration: list enabled connectors, probe
      each, write results, possibly auto-disable.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="connector-health-loop")
        log.info("connector.health.started", interval_s=_settings.connector_health_interval_s)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopped.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        self._task = None
        log.info("connector.health.stopped")

    def _lock_for(self, cid: uuid.UUID) -> asyncio.Lock:
        lock = self._locks.get(cid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cid] = lock
        return lock

    async def _loop(self) -> None:
        interval = max(5, int(_settings.connector_health_interval_s))
        while not self._stopped.is_set():
            try:
                await self.tick_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("connector.health.tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def tick_once(self, session: Optional[AsyncSession] = None) -> int:
        """Run one probe cycle. Returns the number of rows touched.

        Tests call this directly with their own session; the
        production loop constructs one via the app's session
        factory (lazily, to avoid pulling the engine at import time).
        """
        from app.core.database import SessionLocal

        own_session = session is None
        sess = session or SessionLocal()
        try:
            res = await sess.execute(
                select(ModelConnector).where(
                    ModelConnector.is_enabled.is_(True),
                    ModelConnector.deleted_at.is_(None),
                )
            )
            rows = list(res.scalars())
            # Limit the work per cycle so a freshly-restarted app
            # with 1000 connectors doesn't probe them all at once.
            max_per_cycle = max(1, int(_settings.connector_health_max_per_cycle))
            rows = rows[:max_per_cycle]
            for row in rows:
                await self._probe_one(sess, row)
            if own_session:
                await sess.commit()
            return len(rows)
        finally:
            if own_session:
                await sess.close()

    async def _probe_one(
        self, session: AsyncSession, row: ModelConnector
    ) -> None:
        async with self._lock_for(row.id):
            try:
                adapter = _build_adapter(row)
            except (ProviderError, Exception) as exc:  # noqa: BLE001
                log.warning(
                    "connector.health.build_failed",
                    connector_id=str(row.id),
                    error=str(exc),
                )
                return
            try:
                report = await adapter.health_check()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "connector.health.probe_crashed",
                    connector_id=str(row.id),
                    error=str(exc),
                )
                report = None
            finally:
                try:
                    await adapter.aclose()
                except Exception:  # noqa: BLE001
                    pass

            now = datetime.now(timezone.utc)
            row.last_health = report.status if report else "offline"
            row.last_health_at = now
            row.last_health_latency_ms = (
                int(report.latency_ms) if report and report.latency_ms else None
            )
            if report and report.ok:
                row.consecutive_failures = 0
            else:
                row.consecutive_failures = (row.consecutive_failures or 0) + 1
            threshold = max(1, int(_settings.connector_health_failure_threshold))
            if row.consecutive_failures >= threshold and row.is_enabled:
                log.warning(
                    "connector.health.auto_disabled",
                    connector_id=str(row.id),
                    failures=row.consecutive_failures,
                    threshold=threshold,
                )
                row.is_enabled = False
                await audit.record(
                    session,
                    connector_id=row.id,
                    user_id=row.user_id,
                    action=audit.ACTION_UPDATE,
                    before={"is_enabled": True, "last_health": row.last_health},
                    after={"is_enabled": False, "last_health": row.last_health},
                )


def _build_adapter(row: ModelConnector) -> ProviderAdapter:
    """Construct the right adapter for a connector row.

    Mirrors the router's `_build_adapter` but is a copy so the
    health loop can run without depending on the request-time
    router. The two paths MUST stay in sync — a drift here would
    mean the probe uses a different shape than the live call.
    """
    cls = provider_registry.get(row.provider)
    api_key = None
    if row.api_key_enc:
        api_key = crypto.decrypt(row.api_key_enc)
    timeout = float((row.settings or {}).get("timeout_s") or 8.0)
    common: dict = {
        "base_url": row.base_url,
        "api_key": api_key,
        "auth_type": row.auth_type,
        "auth_header_name": row.auth_header_name,
        "custom_headers": row.custom_headers or {},
        "organization_id": row.organization_id,
        "project_id": row.project_id,
        "api_version": row.api_version,
        "timeout_s": timeout,
        "default_model": row.default_model,
        "models": list(row.models or []),
    }
    return cls(**common)


# A module-level singleton; the FastAPI lifespan holds a reference
# to the same instance for the duration of the process.
_probe: Optional[HealthProbe] = None


def get_probe() -> HealthProbe:
    global _probe
    if _probe is None:
        _probe = HealthProbe()
    return _probe


async def start_probe() -> None:
    get_probe().start()


async def stop_probe() -> None:
    p = get_probe()
    await p.stop()


__all__ = [
    "HealthProbe",
    "get_probe",
    "start_probe",
    "stop_probe",
    "tick_once",
]

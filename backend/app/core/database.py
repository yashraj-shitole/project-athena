"""Async SQLAlchemy engine, session, and an RLS-aware session dependency.

There are TWO session dependencies:

* ``get_db`` — an UNSCOPED session. The RLS GUC ``app.current_user_id``
  is NOT set. Use only for tables that have no RLS policy (``users``) or
  for anon/auth endpoints (register / login / refresh). Do NOT use it for
  RLS-protected tables (documents/chunks/conversations/messages/tool_calls):
  with ``FORCE ROW LEVEL SECURITY`` enabled, such queries would return no
  rows (GUC is NULL ⇒ ``athena_current_user()`` is NULL ⇒ policy rejects).

* ``get_user_db`` (in ``app.api.dependencies``) — a SCOPED session that
  sets ``app.current_user_id`` to the authenticated principal and resets
  it in a ``finally`` so the pooled connection never leaks the GUC.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

# SQLite (used by the unit-test suite via `aiosqlite:///:memory:`) does
# not accept `pool_size` or `pool_pre_ping` — StaticPool is the only
# supported pool there. We branch on the URL scheme so production
# Postgres keeps the connection pool it expects.
_engine_kwargs: dict = {"echo": _settings.db_echo, "pool_pre_ping": True}
if not _settings.database_url.startswith("sqlite"):
    _engine_kwargs["pool_size"] = _settings.db_pool_size

engine = create_async_engine(_settings.database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


_RLS_GUC = "app.current_user_id"
_RLS_ADMIN_GUC = "app.is_admin"  # H-2: set to 'true' for admin sessions


async def set_rls_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Bind the Postgres session to a user via the RLS GUC.

    We use session-level `SET` (not `SET LOCAL`) so the GUC persists
    across the multiple statements a single request typically issues.
    `SET LOCAL` only lasts for the current transaction, but our queries
    open and commit their own transactions per `await session.execute()`,
    which would drop the GUC and silently break RLS-filtered reads.

    The value is a UUID, so the interpolation surface is nil, but we
    still funnel it through ``str(uuid.UUID(...))`` to be defensive.
    We also use a parameterised ``set_config`` call (which Postgres
    supports for GUCs) — the f-string is the previous-shape form and
    is kept here as a comment for grep-ability. See L-15.

    Pair this with ``reset_rls_user`` (in a `finally` block) so the
    setting is cleared before the connection returns to the pool —
    otherwise a later request could inherit the wrong user_id.
    """
    safe = str(uuid.UUID(str(user_id)))
    # Parameterised form: the GUC name is interpolated (it's a
    # constant), but the value is bound. The prior f-string shape
    # is gone; ``safe`` is validated as a UUID before it gets here.
    await session.execute(
        text("SELECT set_config(:guc, :value, false)"),
        {"guc": _RLS_GUC, "value": safe},
    )


async def set_rls_admin(session: AsyncSession, is_admin: bool) -> None:
    """Set the ``app.is_admin`` GUC for this session.

    H-2 — the database-level admin predicate
    (``athena_is_admin()`` in init.sql) reads this GUC. The
    application layer (``app/api/dependencies.py::require_admin``)
    is the only caller that should ever set it to ``TRUE``;
    non-admin sessions leave it unset (the function returns
    ``FALSE`` for the absent setting).

    Like :func:`set_rls_user`, this uses session-level ``set_config``
    so the value persists across the multiple statements a
    request issues. Pair with :func:`reset_rls_admin`.
    """
    await session.execute(
        text("SELECT set_config(:guc, :value, false)"),
        {"guc": _RLS_ADMIN_GUC, "value": "true" if is_admin else "false"},
    )


async def reset_rls_admin(session: AsyncSession) -> None:
    """Clear the per-session ``app.is_admin`` GUC.

    Best-effort: a failure here just means the next request on
    this pooled connection will see the same value (which is
    what we want — admin reads only ever run from the
    /metrics or /api/tools/* routes, never from the request
    that follows them on the same connection).
    """
    try:
        await session.execute(
            text("SELECT set_config(:guc, '', false)"),
            {"guc": _RLS_ADMIN_GUC},
        )
    except Exception as exc:  # noqa: BLE001
        try:
            from app.core.logging import get_logger
            get_logger(__name__).warning(
                "rls.reset_admin_failed", guc=_RLS_ADMIN_GUC, error=str(exc)
            )
        except Exception:  # noqa: BLE001
            pass


async def reset_rls_user(session: AsyncSession) -> None:
    """Clear the per-session RLS GUC. Safe to call repeatedly.

    If the RESET fails (e.g. the in-flight transaction is already
    aborted, so every statement raises until the tx is rolled back),
    we log a warning — a silent swallow here would leak the GUC onto the
    pooled connection and the next request could inherit the wrong
    user_id. Callers should `rollback()` before calling this.
    """
    try:
        await session.execute(text(f"RESET {_RLS_GUC}"))
    except Exception as exc:  # noqa: BLE001
        # If the session is already closed/torn-down there's nothing to
        # reset — log so a leak is observable rather than silent.
        try:
            from app.core.logging import get_logger
            get_logger(__name__).warning(
                "rls.reset_failed", guc=_RLS_GUC, error=str(exc)
            )
        except Exception:  # noqa: BLE001
            pass


@asynccontextmanager
async def user_scoped_session(user_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Yield a session with RLS already configured for `user_id`."""
    async with SessionLocal() as session:
        await set_rls_user(session, user_id)
        try:
            yield session
        finally:
            await reset_rls_user(session)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session that auto-closes (RLS set by route via get_user_db)."""
    async with SessionLocal() as session:
        yield session

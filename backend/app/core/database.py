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

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_size=_settings.db_pool_size,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


_RLS_GUC = "app.current_user_id"


async def set_rls_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Bind the Postgres session to a user via the RLS GUC.

    We use session-level `SET` (not `SET LOCAL`) so the GUC persists
    across the multiple statements a single request typically issues.
    `SET LOCAL` only lasts for the current transaction, but our queries
    open and commit their own transactions per `await session.execute()`,
    which would drop the GUC and silently break RLS-filtered reads.

    The value is a UUID, so the interpolation surface is nil, but we
    still funnel it through ``str(uuid.UUID(...))`` to be defensive.

    Pair this with ``reset_rls_user`` (in a `finally` block) so the
    setting is cleared before the connection returns to the pool —
    otherwise a later request could inherit the wrong user_id.
    """
    safe = str(uuid.UUID(str(user_id)))
    await session.execute(text(f"SET {_RLS_GUC} = '{safe}'"))


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

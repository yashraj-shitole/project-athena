"""Shared FastAPI dependencies for API routes."""
from __future__ import annotations

import uuid
from typing import Annotated, AsyncIterator

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user_id
from app.core.database import (
    reset_rls_admin,
    reset_rls_user,
    set_rls_admin,
    set_rls_user,
)
from app.models.user import User

# Re-export the standard aliases
CurrentUserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


async def require_admin(current: "CurrentUser") -> User:
    """Authorize the user as a tool administrator.

    Admins are configured via `ATHENA_ADMIN_EMAILS` (comma-separated).
    If the allowlist is empty, every admin-gated endpoint is disabled
    (403) — a fresh deploy should NOT expose tool mutation to all users.
    """
    settings = get_settings()
    allow = {e.strip().lower() for e in settings.admin_emails if e.strip()}
    if not allow:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tool administration is disabled (no admins configured).",
        )
    if not current.email or current.email.lower() not in allow:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required.",
        )
    return current


# Admin dependency: an authenticated, active user that is in the admin allowlist.
AdminUser = Annotated[User, Depends(require_admin)]


async def get_user_db(
    user_id: CurrentUserId,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[AsyncSession]:
    """DB session with the RLS GUC set to the current user.

    Yields the session for the request, then clears the GUC in a
    `finally` so the underlying connection can be returned to the pool
    without leaking the user_id to the next request.

    We `rollback()` before `reset_rls_user()`: if the request raised a
    DB error the PostgreSQL transaction enters the aborted state and
    every statement (including `RESET app.current_user_id`) raises until
    the tx is rolled back. Without this, the RESET would raise, the bare
    `except` in `reset_rls_user` would swallow it, and the GUC would
    silently persist on the pooled connection — leaking the user_id to
    the next request that reuses it.
    """
    await set_rls_user(session, user_id)
    # The caller is, by definition, not an admin. Set the admin
    # GUC to FALSE so the database-level ``athena_is_admin()``
    # predicate (see init.sql) cannot be tricked by a leftover
    # TRUE from a previous request on the same pooled connection.
    await set_rls_admin(session, is_admin=False)
    try:
        yield session
    finally:
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        await reset_rls_user(session)
        await reset_rls_admin(session)


async def get_admin_db(
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[AsyncSession]:
    """DB session with both RLS GUCs set: ``user_id`` and ``is_admin``.

    H-2 — the database-level ``athena_is_admin()`` predicate reads
    ``app.is_admin``. This dependency is the *only* session-yielding
    dependency that sets it to TRUE; the ``require_admin`` dep
    guards the route, and we set the GUC after that check passes.

    Like :func:`get_user_db`, both GUCs are reset in ``finally``
    so the next request on the pooled connection cannot inherit
    the admin bit.
    """
    await set_rls_user(session, admin.id)
    await set_rls_admin(session, is_admin=True)
    try:
        yield session
    finally:
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        await reset_rls_admin(session)
        await reset_rls_user(session)


DbSession = Annotated[AsyncSession, Depends(get_user_db)]
AdminDbSession = Annotated[AsyncSession, Depends(get_admin_db)]


async def get_current_user(
    user_id: CurrentUserId,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Fetch the full User row for the current request."""
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None or not user.is_active:
        # Re-use the same 401 the auth dep produces
        from fastapi import HTTPException, status as http_status

        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

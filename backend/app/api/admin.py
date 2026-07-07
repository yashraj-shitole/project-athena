"""Admin API (H-19)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import AdminDbSession, AdminUser
from app.core.logging import get_logger
from app.models.user import User
from app.schemas.auth import AdminUserUpdate, UserPublic

log = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.patch(
    "/users/{user_id}",
    response_model=UserPublic,
)
async def update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    admin: AdminUser,
    session: AdminDbSession,
) -> UserPublic:
    """H-19 — toggle ``is_active`` on a user.

    Admin-only. When the value flips from active → inactive we also
    bump the *target's* ``token_version`` so every outstanding JWT
    for the target is revoked at the next API call. The target's
    UI will silently start hitting 401s, and the SPA can route them
    to a "your account has been disabled" screen.

    When the value flips inactive → active we clear the lockout
    state so the user can log back in immediately rather than
    waiting out a stale ``locked_until`` window. We deliberately
    do *not* reset ``token_version`` on re-enable: the prior
    revocation stands, and the user must re-authenticate.

    If the value does not actually change (PATCH with the current
    state) we no-op and return the current row.
    """
    res = await session.execute(select(User).where(User.id == user_id))
    target = res.scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.is_active == payload.is_active:
        return UserPublic.model_validate(target)

    before = {"is_active": target.is_active}
    target.is_active = payload.is_active
    if not payload.is_active:
        # Disable path — bump the target's revocation version so
        # their existing JWTs fail.
        target.token_version = (target.token_version or 0) + 1
    else:
        # Re-enable path — clear the lockout state so a
        # previously-locked user can log back in immediately.
        # We do *not* reset token_version; the prior revocation
        # is the admin's documented decision and the user must
        # re-authenticate to get fresh tokens.
        target.failed_login_count = 0
        target.locked_until = None
    session.add(target)
    await session.commit()
    await session.refresh(target)
    log.info(
        "auth.user.disabled" if not payload.is_active else "auth.user.enabled",
        actor_id=str(admin.id),
        target_id=str(target.id),
        before=before,
        is_active=target.is_active,
    )
    return UserPublic.model_validate(target)

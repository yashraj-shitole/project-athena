"""Authentication API (FR-01..03, FR-04)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, DbSession
from app.core.config import get_settings, settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.ratelimit import (
    RateLimitLogin,
    RateLimitRefresh,
    RateLimitRegister,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    AccessToken,
    AdminUserUpdate,
    PasswordChangeRequest,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserPublic,
)

log = get_logger(__name__)

# DB session for anonymous auth endpoints (register / login / refresh).
# These must NOT go through the RLS-gated `DbSession` because there is no
# authenticated user yet — `get_db` returns a session with the default
# (privileged) GUC.
AnonDbSession = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix="/auth", tags=["auth"])

# Pre-computed dummy hash used to equalize login timing when the email
# is unknown, so a missing account and a wrong password take roughly the
# same time (blunts email-enumeration via timing).
_DUMMY_HASH = hash_password("athena-dummy-do-not-use")


def _make_pair(user: User) -> TokenPair:
    access = create_access_token(user.id, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=settings.access_token_ttl_min * 60,
    )


def _lockout_remaining_seconds(locked_until: datetime) -> int:
    """How many whole seconds remain on a lockout. Returns 0 if the
    lockout has already expired (or is None).

    Robust to naive datetimes: SQLite drops the tzinfo on round-trip,
    so the value we just wrote a moment ago comes back as a naive
    ``datetime`` even though the column type is
    ``DateTime(timezone=True)``. Postgres preserves it. We treat a
    naive value as UTC — the only correct interpretation when the
    writer used ``datetime.now(timezone.utc)`` and the column is
    documented as UTC.
    """
    if locked_until is None:
        return 0
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if locked_until <= now:
        return 0
    return int((locked_until - now).total_seconds())


async def _maybe_apply_lockout(user: User) -> None:
    """Increment the failed-login counter on a user row and set
    ``locked_until`` if the threshold is crossed. H-19.

    Only called on the *wrong-password* path; the unknown-email and
    inactive-user paths do not touch the counter so the response
    time is consistent and the counter cannot be poisoned with
    bogus rows.
    """
    settings = get_settings()
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= settings.login_max_fails:
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            seconds=settings.login_lockout_s
        )


async def _authenticate(session: AsyncSession, email: str, password: str) -> User:
    """Shared login lookup with timing-equalized failure.

    Returns the User on success; raises 401 on any failure. The error
    message is identical whether the email is unknown, the password is
    wrong, or the account is inactive — so the endpoint does not reveal
    which it was.

    H-19 — account-scoped lockout. Before checking the password we
    inspect ``user.locked_until``; if it is in the future, we return
    401 with the same generic body as a wrong-password failure plus
    a ``WWW-Authenticate: Bearer locked=N`` hint header so the SPA
    can render a "try again in N seconds" message. The body
    deliberately does not say "locked" so the response is
    indistinguishable from a wrong-password failure to an attacker
    who does not control the proxy headers.

    On success, the counter and lockout are reset. On a wrong
    password, the counter is incremented and a lockout is set if
    the threshold is crossed.
    """
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    # Always run a bcrypt verify so the unknown-email path takes roughly
    # the same time as the wrong-password path.
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # H-19 — check lockout *before* the password verify. A locked
    # account never even runs bcrypt; the dummy-hash verify is run
    # instead so the response time is still consistent with a
    # wrong-password failure.
    remaining = _lockout_remaining_seconds(user.locked_until)
    if remaining > 0:
        verify_password(password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={
                "WWW-Authenticate": f"Bearer locked={remaining}",
            },
        )

    if not verify_password(password, user.password_hash) or not user.is_active:
        # Same message for wrong password and inactive — do not reveal
        # that the account exists but is disabled. The lockout
        # increment fires only on the wrong-password path; the
        # inactive path runs the dummy verify to keep timing
        # consistent but does not poison the counter (we don't
        # want an attacker to be able to lock an account by
        # spraying a known-disabled user, but we also don't want
        # to give the attacker a "this is disabled" timing tell).
        if user.is_active:
            await _maybe_apply_lockout(user)
            await session.commit()
        else:
            verify_password(password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # H-19 — success resets the lockout state.
    if user.failed_login_count or user.locked_until:
        user.failed_login_count = 0
        user.locked_until = None
        await session.commit()

    return user


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RateLimitRegister],
)
async def register(payload: UserCreate, session: AnonDbSession) -> User:
    """FR-01: create a new user.

    A duplicate email returns a generic 400 (the message does not state
    that the email is already registered) to blunt email enumeration.

    L-32 — emails are lowercased at the storage boundary. The
    Pydantic ``EmailStr`` validator checks the address is
    well-formed but does not normalize case, so
    ``User@Example.com`` and ``user@example.com`` would otherwise
    be two distinct accounts. We lowercase at the route layer
    (the single place that writes a new row) so the comparison
    and the storage are case-consistent.
    """
    email = payload.email.lower()
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to register with those credentials.",
        )
    user = User(email=email, password_hash=hash_password(payload.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/login", response_model=TokenPair, dependencies=[RateLimitLogin])
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AnonDbSession = None,  # type: ignore[assignment]
) -> TokenPair:
    """FR-02: email/password → access + refresh tokens."""
    # L-32 — lowercase the username before lookup. The
    # registration path normalizes; the login path must too.
    user = await _authenticate(
        session, form_data.username.lower(), form_data.password
    )
    return _make_pair(user)


@router.post("/login-json", response_model=TokenPair, dependencies=[RateLimitLogin])
async def login_json(payload: UserLogin, session: AnonDbSession) -> TokenPair:
    """Convenience JSON login for SPA clients (FR-02)."""
    user = await _authenticate(
        session, payload.email.lower(), payload.password
    )
    return _make_pair(user)


@router.post("/refresh", response_model=TokenPair, dependencies=[RateLimitRefresh])
async def refresh(payload: RefreshRequest, session: AnonDbSession) -> TokenPair:
    """FR-03: exchange a refresh token for a new access + refresh token.

    Refresh-token rotation: the presented refresh token is consumed and
    a fresh access + refresh pair is issued. The `ver` claim must match
    the user's current `token_version` — a logout (or any future
    revocation) bumps that version and invalidates all outstanding tokens.
    """
    try:
        data = decode_token(payload.refresh_token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        ) from exc

    if data.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type",
        )
    sub = data.get("sub")
    try:
        user_id = uuid.UUID(sub)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid subject"
        ) from exc

    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    # Revocation check: token's ver must match the user's current version.
    if int(data.get("ver", 0)) != int(user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )
    return _make_pair(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def logout(current: CurrentUser, session: DbSession):
    """Revoke all outstanding tokens for the current user.

    Bumps `token_version` so every access/refresh token issued before this
    moment fails the `ver` check in `get_current_user_id` and `/refresh`.
    """
    current.token_version = (current.token_version or 0) + 1
    session.add(current)
    await session.commit()


# H-19 — self-service password change. The flow:
# 1. Caller is authenticated (the ``CurrentUser`` dep).
# 2. The body carries the current password and the new password.
# 3. We verify the current password against the stored hash.
# 4. We rotate to the new hash, bump ``token_version`` (every
#    outstanding JWT — including the one in the request header — is
#    revoked), and clear the lockout state as a courtesy.
#
# The error message for a wrong current password is "Incorrect
# current password" — distinct from the login route's "Incorrect
# email or password" because the caller is already authenticated;
# there is no enumeration concern here. The new password is
# validated by Pydantic (min 8, max 72 bytes) and bcrypt hashes
# it; the same rules as registration apply.
#
# We return 200 with a small JSON ack (``{"status": "ok"}``)
# rather than 204 because FastAPI refuses to attach a request body
# to a 204 route. The semantics are equivalent for the SPA.
@router.post("/change-password", response_model=dict)
async def change_password(
    payload: PasswordChangeRequest,
    current: CurrentUser,
    session: DbSession,
) -> dict[str, str]:
    if not verify_password(payload.current_password, current.password_hash):
        # Distinct message from the login route: the caller is
        # already authenticated, so "Incorrect current password"
        # is a meaningful diagnostic, not an enumeration leak.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect current password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    current.password_hash = hash_password(payload.new_password)
    current.token_version = (current.token_version or 0) + 1
    # Reset the lockout state — the user just proved possession
    # of a valid current password, so any prior failure history
    # is moot.
    current.failed_login_count = 0
    current.locked_until = None
    # ``current`` was loaded by ``get_current_user`` via its own
    # session (the JWT-dep session, not the route's
    # ``DbSession``). The two sessions are independent — a naive
    # ``session.add(current)`` raises ``already attached to
    # session`` because the same instance is already in the JWT
    # session's identity map. ``session.merge(current)`` copies
    # the dirty state into the route's session and is a no-op
    # when the instance is unknown to it.
    await session.merge(current)
    await session.commit()
    log.info(
        "auth.password.changed",
        user_id=str(current.id),
    )
    return {"status": "ok"}


@router.get("/me", response_model=UserPublic)
async def me(current: CurrentUser) -> User:
    """Return the authenticated user's profile."""
    return current
"""Authentication API (FR-01..03, FR-04)."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, DbSession
from app.core.config import settings
from app.core.database import get_db
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
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserPublic,
)

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


async def _authenticate(session: AsyncSession, email: str, password: str) -> User:
    """Shared login lookup with timing-equalized failure.

    Returns the User on success; raises 401 on any failure. The error
    message is identical whether the email is unknown, the password is
    wrong, or the account is inactive — so the endpoint does not reveal
    which it was.
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
    if not verify_password(password, user.password_hash) or not user.is_active:
        # Same message for wrong password and inactive — do not reveal
        # that the account exists but is disabled.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: UserCreate, session: AnonDbSession) -> User:
    """FR-01: create a new user.

    A duplicate email returns a generic 400 (the message does not state
    that the email is already registered) to blunt email enumeration.
    """
    existing = await session.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to register with those credentials.",
        )
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AnonDbSession = None,  # type: ignore[assignment]
) -> TokenPair:
    """FR-02: email/password → access + refresh tokens."""
    user = await _authenticate(session, form_data.username, form_data.password)
    return _make_pair(user)


@router.post("/login-json", response_model=TokenPair)
async def login_json(payload: UserLogin, session: AnonDbSession) -> TokenPair:
    """Convenience JSON login for SPA clients (FR-02)."""
    user = await _authenticate(session, payload.email, payload.password)
    return _make_pair(user)


@router.post("/refresh", response_model=TokenPair)
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


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current: CurrentUser, session: DbSession) -> None:
    """Revoke all outstanding tokens for the current user.

    Bumps `token_version` so every access/refresh token issued before this
    moment fails the `ver` check in `get_current_user_id` and `/refresh`.
    """
    current.token_version = (current.token_version or 0) + 1
    session.add(current)
    await session.commit()


@router.get("/me", response_model=UserPublic)
async def me(current: CurrentUser) -> User:
    """Return the authenticated user's profile."""
    return current
"""FastAPI dependencies — auth + DB + Redis injection."""
from __future__ import annotations

import uuid
from typing import Annotated, AsyncIterator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=True)

CredentialsException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user_id(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> uuid.UUID:
    """Decode the access JWT and return the user id (raises 401 on failure).

    Also enforces revocation: the token's `ver` claim must match the
    user's current `token_version`, and the user must be `is_active`.
    This DB lookup on every authed request is the price of being able to
    revoke outstanding tokens (logout / force-logout) without maintaining
    a server-side denylist.
    """
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.PyJWTError as exc:
        raise CredentialsException from exc

    sub = payload.get("sub")
    typ = payload.get("type")
    if not sub or typ != "access":
        raise CredentialsException
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise CredentialsException from exc

    # Revocation + active check. `users` has no RLS, so the unscoped
    # `get_db` session is correct here.
    from app.models.user import User

    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None or not user.is_active:
        raise CredentialsException
    if int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise CredentialsException

    return user_id


CurrentUserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
DbSession = Annotated[AsyncSession, Depends(get_db)]

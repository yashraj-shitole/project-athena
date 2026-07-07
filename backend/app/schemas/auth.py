"""Authentication & user schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.base import ORMModelBase, RequestBase


class UserCreate(RequestBase):
    email: EmailStr
    # bcrypt silently truncates inputs to 72 bytes, so two distinct long
    # passwords would hash to the same value. Cap at 72 to make the limit
    # explicit and reject collisions at the boundary instead.
    password: str = Field(min_length=8, max_length=72)


class UserLogin(RequestBase):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


# H-19 — the user-facing "change my password" flow. Requires the
# current password (so a stolen device can't rotate out the legitimate
# user without the old secret) and the new password (with the same
# 8..72 byte rule as registration). The route bumps ``token_version``
# on success, so all outstanding JWTs are revoked in one shot.
class PasswordChangeRequest(RequestBase):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


# H-19 — the admin-only "disable this user" flow. Today the only
# field is ``is_active``; future fields (e.g. ``is_admin``) can be
# added here. ``RequestBase`` enforces ``extra="forbid"`` so a
# non-admin cannot smuggle additional fields through the admin
# router (defense in depth on top of the AdminUser dep).
class AdminUserUpdate(RequestBase):
    is_active: bool


class UserPublic(ORMModelBase):
    id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime


class TokenPair(ORMModelBase):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access_token expiry


class AccessToken(ORMModelBase):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(RequestBase):
    refresh_token: str

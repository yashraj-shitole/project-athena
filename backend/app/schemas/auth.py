"""Authentication & user schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.base import ORMModelBase


class UserCreate(ORMModelBase):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(ORMModelBase):
    email: EmailStr
    password: str


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


class RefreshRequest(ORMModelBase):
    refresh_token: str

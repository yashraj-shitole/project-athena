"""User ORM model."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Monotonic revocation version, embedded in issued JWTs as `ver`.
    # Bumping this invalidates all outstanding access/refresh tokens for
    # the user (they no longer match). Used by /auth/logout and any
    # future password-change / force-logout flow.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # H-19 — account-scoped lockout. ``failed_login_count`` is the
    # number of consecutive wrong-password attempts since the last
    # successful login (or counter reset). When it crosses
    # ``settings.login_max_fails`` we set ``locked_until`` to
    # ``now() + settings.login_lockout_s`` and the user cannot log
    # in until that timestamp passes. Both fields are reset to
    # defaults on every successful login (and on admin re-enable).
    failed_login_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

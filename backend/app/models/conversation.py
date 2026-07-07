"""Conversation + Message ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.seq",
        lazy="selectin",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # user|assistant|system|tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    used_tools: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    seq: Mapped[int] = mapped_column(
        BigInteger,
        server_default=func.nextval(
            # We rely on the BIGSERIAL sequence auto-created in init.sql.
            "messages_seq"
        ),
        nullable=False,
    )
    # Which connector / model produced this message. NULL for messages
    # produced before the EMC module shipped, or for messages that did
    # not invoke a model (e.g. `user` messages — the user picker fills
    # this in for the *assistant* reply on its own row).
    connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

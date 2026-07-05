"""Conversation + message + chat schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List

from pydantic import Field

from app.schemas.base import ORMModelBase
from app.schemas.chunk import Citation


class ConversationCreate(ORMModelBase):
    title: str | None = None


class ConversationPublic(ORMModelBase):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessagePublic(ORMModelBase):
    id: uuid.UUID
    seq: int
    role: str
    content: str
    citations: List[Citation] = Field(default_factory=list)
    used_tools: List[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ChatRequest(ORMModelBase):
    """Non-streaming or initiator for a streaming chat turn."""

    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    # FR-30: explicitly request a tool subset; empty = orchestrator selects.
    tool_subset: List[str] | None = None
    stream: bool = False


class ChatResponse(ORMModelBase):
    """Non-streaming structured response (NFR-1 / FR-2)."""

    conversation_id: uuid.UUID
    message: MessagePublic

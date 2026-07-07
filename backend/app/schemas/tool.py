"""Tool registry + tool-call audit schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.base import ORMModelBase, RequestBase


class ToolPublic(ORMModelBase):
    id: uuid.UUID
    name: str
    version: int
    description: str
    parameters: dict[str, Any]
    handler_type: str
    enabled: bool
    is_builtin: bool
    updated_at: datetime


class ToolUpsert(RequestBase):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    handler_type: str  # internal|http|mcp
    handler_cfg: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ToolCallPublic(ORMModelBase):
    id: uuid.UUID
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    status: str
    latency_ms: int | None
    created_at: datetime

"""Document metadata schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.base import ORMModelBase


class DocumentPublic(ORMModelBase):
    id: uuid.UUID
    filename: str
    file_type: str
    size_bytes: int
    page_count: int | None = None
    status: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(ORMModelBase):
    items: list[DocumentPublic]
    total: int


class DocumentStatusEvent(ORMModelBase):
    """SSE-shaped document status update (FR-4 ingest status)."""

    document_id: uuid.UUID
    status: str
    error_message: str | None = None
    chunks_indexed: int | None = Field(default=None)

"""Document metadata schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

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
    # --- progress + post-completion metadata ---
    current_stage: str | None = None
    stage_progress: dict[str, Any] = Field(default_factory=dict)
    chunk_count: int | None = None
    embedding_model: str | None = None
    started_at: datetime | None = None
    processed_at: datetime | None = None
    processing_time_ms: int | None = None
    # ---
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(ORMModelBase):
    items: list[DocumentPublic]
    total: int


class DocumentStatusEvent(ORMModelBase):
    """SSE-shaped document status update (FR-4 ingest status).

    Sent as `STATE` on connect and on every reconnecting client. The
    payload mirrors the document row so a fresh browser tab can render
    the current state without a separate GET. Live-streamed `PROGRESS`
    / `STAGE` / `TERMINAL` events keep it up to date as the pipeline
    runs.
    """

    document_id: uuid.UUID
    status: str
    error_message: str | None = None
    current_stage: str | None = None
    stage_progress: dict[str, Any] = Field(default_factory=dict)
    chunk_count: int | None = None
    page_count: int | None = None
    embedding_model: str | None = None
    started_at: datetime | None = None
    processed_at: datetime | None = None
    processing_time_ms: int | None = None

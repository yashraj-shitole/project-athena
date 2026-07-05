"""Shared Pydantic base + mixins used across all schema modules."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ORMModelBase(BaseModel):
    """Base for ORM-derived schemas. Enables `from_attributes=True` (Pydantic v2)."""

    model_config = {"from_attributes": True}


class TimestampedMixin:
    created_at: datetime
    updated_at: datetime | None = None

"""Shared Pydantic base + mixins used across all schema modules.

Two base classes:

* ``ORMModelBase`` — for response / output shapes that mirror an ORM
  row. Permissive by design (extra fields allowed, ``from_attributes``
  enabled).
* ``RequestBase`` — for *request* shapes (POST/PATCH bodies). Locks
  down ``extra="forbid"`` so unknown fields are rejected with a 422
  instead of being silently ignored. This is the Pydantic v2
  replacement for the old ``Config.extra = "forbid"`` and is the
  primary defense against mass-assignment (H-22).

The two are intentionally separate: many response shapes are
*forgiving* (they tolerate extra columns being added to the ORM and
just drop them), while request shapes must be *strict* (the caller
should not be able to smuggle a field the server does not know about).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModelBase(BaseModel):
    """Base for ORM-derived response schemas.

    Enables ``from_attributes=True`` (Pydantic v2) so the public
    shapes can be constructed directly from ORM rows. Permissive on
    extras — see :class:`RequestBase` for the strict variant.
    """

    model_config = ConfigDict(from_attributes=True)


class RequestBase(BaseModel):
    """Base for *request* schemas (POST / PATCH / PUT bodies).

    Enforces:

    * ``extra="forbid"`` — unknown fields are rejected with a 422.
      This is the Pydantic v2 way to prevent mass-assignment: a
      payload that contains a field the schema does not declare
      (e.g. ``is_admin``, ``user_id``) cannot be silently swallowed
      and the request fails loud.
    * ``from_attributes=True`` — kept for parity with response shapes.

    Response shapes should continue to use :class:`ORMModelBase` —
    the strictness here is asymmetric on purpose.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class TimestampedMixin:
    created_at: datetime
    updated_at: datetime | None = None

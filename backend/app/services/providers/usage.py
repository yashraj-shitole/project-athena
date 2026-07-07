"""Per-request usage tracking for connectors.

Usage rows are written from two places:

  1. `LLMClient.complete()` / `LLMClient.stream()` — every chat turn.
  2. `health_check()` in the background probe.

We do NOT block the request on the write. A `record()` call simply adds
the row to the session; the caller commits it. If the session aborts,
the usage row is dropped too (acceptable: the next request creates a
new one, and an aborted run was probably a user-initiated cancel).

The aggregation is intentionally a separate function (aggregate) so the
caller can swap the storage layer (e.g. push to a time-series DB) without
touching the write path.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.connector import ConnectorUsage

log = get_logger(__name__)

# Stable status vocabulary. Mirrored on the dashboard; rename only with
# a coordinated frontend change.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_AUTH_FAILED = "auth_failed"
STATUS_CANCELLED = "cancelled"
STATUS_STREAM_INTERRUPTED = "stream_interrupted"
STATUSES: tuple[str, ...] = (
    STATUS_OK,
    STATUS_ERROR,
    STATUS_TIMEOUT,
    STATUS_RATE_LIMITED,
    STATUS_AUTH_FAILED,
    STATUS_CANCELLED,
    STATUS_STREAM_INTERRUPTED,
)


def record(
    session: AsyncSession,
    *,
    connector_id: uuid.UUID,
    user_id: uuid.UUID,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    status: str = STATUS_OK,
    error_class: Optional[str] = None,
    cost_estimate: Decimal | float | int | str = 0,
) -> ConnectorUsage:
    """Add a usage row to the session. Caller commits.

    `cost_estimate` is stored as text (the column is TEXT) so it stays
    exact for any precision — we coerce through Decimal on the way in.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown usage status: {status!r}")
    cost_text = (
        str(Decimal(str(cost_estimate)))
        if not isinstance(cost_estimate, Decimal)
        else str(cost_estimate)
    )
    row = ConnectorUsage(
        connector_id=connector_id,
        user_id=user_id,
        model=model,
        prompt_tokens=max(0, int(prompt_tokens)),
        completion_tokens=max(0, int(completion_tokens)),
        latency_ms=max(0, int(latency_ms)),
        status=status,
        error_class=(error_class or "")[:120] or None,
        cost_estimate=cost_text,
    )
    session.add(row)
    return row


async def aggregate(
    session: AsyncSession,
    *,
    connector_id: uuid.UUID,
    days: int = 7,
) -> dict[str, Any]:
    """Return dashboard-shaped aggregates for the last `days` days.

    Shape:
        {
            "total_requests": int,
            "total_prompt_tokens": int,
            "total_completion_tokens": int,
            "avg_latency_ms": float,
            "success_rate": float,
            "by_day": [
                {"day": "2026-07-01", "requests": int,
                 "prompt_tokens": int, "completion_tokens": int,
                 "avg_latency_ms": float, "errors": int},
                ...
            ]
        }
    """
    days = max(1, min(int(days), 90))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 1. Totals.
    totals = (
        await session.execute(
            select(
                func.count(ConnectorUsage.id),
                func.coalesce(func.sum(ConnectorUsage.prompt_tokens), 0),
                func.coalesce(func.sum(ConnectorUsage.completion_tokens), 0),
                func.coalesce(func.avg(ConnectorUsage.latency_ms), 0.0),
                func.coalesce(
                    func.sum(
                        func.cast(ConnectorUsage.status == STATUS_OK, Integer)
                    ),
                    0,
                ),
            ).where(
                ConnectorUsage.connector_id == connector_id,
                ConnectorUsage.at >= since,
            )
        )
    ).one()
    total_requests = int(totals[0] or 0)
    total_prompt_tokens = int(totals[1] or 0)
    total_completion_tokens = int(totals[2] or 0)
    avg_latency_ms = float(totals[3] or 0.0)
    successes = int(totals[4] or 0)
    success_rate = (successes / total_requests) if total_requests else 0.0

    # 2. Per-day buckets. Use date_trunc('day', at) — UTC.
    bucket = func.date_trunc("day", ConnectorUsage.at).label("day")
    per_day_rows = (
        await session.execute(
            select(
                bucket,
                func.count(ConnectorUsage.id).label("requests"),
                func.coalesce(func.sum(ConnectorUsage.prompt_tokens), 0).label(
                    "prompt_tokens"
                ),
                func.coalesce(func.sum(ConnectorUsage.completion_tokens), 0).label(
                    "completion_tokens"
                ),
                func.coalesce(func.avg(ConnectorUsage.latency_ms), 0.0).label(
                    "avg_latency_ms"
                ),
                func.coalesce(
                    func.sum(
                        func.cast(ConnectorUsage.status != STATUS_OK, Integer)
                    ),
                    0,
                ).label("errors"),
            )
            .where(
                ConnectorUsage.connector_id == connector_id,
                ConnectorUsage.at >= since,
            )
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    by_day = [
        {
            "day": r.day.date().isoformat() if hasattr(r.day, "date") else str(r.day),
            "requests": int(r.requests or 0),
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "avg_latency_ms": float(r.avg_latency_ms or 0.0),
            "errors": int(r.errors or 0),
        }
        for r in per_day_rows
    ]
    return {
        "total_requests": total_requests,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "avg_latency_ms": avg_latency_ms,
        "success_rate": success_rate,
        "by_day": by_day,
    }


# `Integer` is imported at the top of the module for the bool-cast
# trick in `aggregate`.

__all__ = [
    "record",
    "aggregate",
    "STATUSES",
] + list(STATUSES)

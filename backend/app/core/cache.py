"""Async Redis cache wrapper with namespacing and JSON helpers."""
from __future__ import annotations

import json
from typing import Any, Optional

import redis.asyncio as redis_async

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_settings = get_settings()
_client: Optional[redis_async.Redis] = None


def get_client() -> redis_async.Redis:
    global _client
    if _client is None:
        _client = redis_async.from_url(
            _settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _client


# -------- counters (FR-36) --------
HITS = "athena:cache:hits"
MISSES = "athena:cache:misses"


async def _bump(counter: str) -> None:
    try:
        await get_client().incr(counter)
    except Exception:  # noqa: BLE001
        # metrics are best-effort; do not fail the request
        pass


def _k(ns: str, key: str) -> str:
    return f"athena:{ns}:{key}"


# -------- JSON helpers --------
async def get_json(ns: str, key: str) -> Optional[Any]:
    """Fail-open: a Redis outage degrades to a cache miss, never a 500."""
    try:
        raw = await get_client().get(_k(ns, key))
    except Exception as exc:  # noqa: BLE001
        log.warning("cache.get_json.error", ns=ns, error=str(exc))
        await _bump(MISSES)
        return None
    if raw is None:
        await _bump(MISSES)
        return None
    await _bump(HITS)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("cache.get_json.bad_value", ns=ns, error=str(exc))
        await _bump(MISSES)
        return None


async def set_json(ns: str, key: str, value: Any, ttl: Optional[int] = None) -> None:
    """Fail-open: a Redis outage skips the write, never a 500."""
    ttl = ttl if ttl is not None else _settings.cache_ttl_seconds
    try:
        await get_client().set(_k(ns, key), json.dumps(value, default=str), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        log.warning("cache.set_json.error", ns=ns, error=str(exc))


async def delete_pattern(pattern: str) -> int:
    """Delete all keys matching `pattern` (within the `user` namespace)."""
    c = get_client()
    deleted = 0
    try:
        async for k in c.scan_iter(match=_k("user", pattern), count=200):
            await c.delete(k)
            deleted += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("cache.delete_pattern.error", pattern=pattern, error=str(exc))
    return deleted


async def invalidate_user(user_id: str, prefix: str | None = None) -> None:
    """Drop all per-user cache entries.

    If `prefix` is given, only entries whose key contains that prefix are
    removed. Otherwise, every per-user key is dropped across all the
    per-user namespaces we maintain (currently: `user`, `search`).
    """
    c = get_client()
    # These are the cache namespaces that include a `user_id` token in
    # the key. We must keep this list in sync with the prefixes we use
    # elsewhere in the codebase (`_settings.CACHE_PREFIX_*`).
    namespaces = ("user", "search")
    user_token = f"{user_id}:"
    deleted = 0
    for ns in namespaces:
        pattern = f"athena:{ns}:{user_token}*"
        async for k in c.scan_iter(match=pattern, count=200):
            if prefix is None or prefix in k:
                await c.delete(k)
                deleted += 1
    if deleted:
        log.debug("cache.invalidate_user", user_id=user_id, prefix=prefix, deleted=deleted)


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None

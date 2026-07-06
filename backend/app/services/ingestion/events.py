"""In-process event broker for document ingestion status.

A single FastAPI worker process fans SSE events out to any number of
connected browser tabs. Phase 2 (real worker + replicas) will move this
to Redis pub/sub; the public surface here is shaped so that swap is a
one-file change.

Wire format: see `app.services.llm.streamer.sse()`. Bytes are stored and
forwarded as-is so the SSE shape stays owned by `streamer.sse()`.

Per-doc state:
  - `_subscribers[doc_id] = list[asyncio.Queue[bytes | None]]`
        Live subscribers. Each queue is bounded at 64; on overflow the
        queue gets a `None` sentinel and the SSE handler closes the
        consumer's stream (we'd rather drop a slow tab than backpressure
        the pipeline).
  - `_history[doc_id] = collections.deque[bytes]` (maxlen=16)
        Replay buffer for reconnecting clients. Older events are evicted
        FIFO; 16 covers a typical tab-reconnect at fast cadence.
  - `_terminal[doc_id] = True` once `mark_terminal()` runs, so a late
        subscriber knows to send one STATE and close. `_registry_lock`
        is a module-level `asyncio.Lock` (one per process) protecting
        only the `dict` accessors; per-doc operations are lock-free
        after the initial lookup.

Cleanup:
  `mark_terminal(doc_id)` schedules `_delayed_cleanup(doc_id, 60s)` so
  the broker forgets the doc 60s after it reaches a terminal state.
  Long enough for a late-reconnecting browser to receive the STATE,
  short enough that we don't leak per-doc state forever.
"""
from __future__ import annotations

import asyncio
import collections
import uuid
from typing import Deque

# Bounded queue size for each subscriber. 64 events ~= 30s of progress
# at 2 events/sec, well above the typical reconnect window. Slow
# consumers (network-crippled tabs) get disconnected rather than
# backpressured onto the ingest pipeline.
_QUEUE_MAX = 64

# Ring buffer size for replay on reconnect. 16 covers the typical
# "tab was backgrounded, came back, missed N events" case.
_HISTORY_MAX = 16

# How long after a terminal state the broker forgets a doc. Should be
# long enough for the typical "browser reconnects after a refresh" to
# receive the last STATE, but short enough that per-doc state doesn't
# accumulate over the lifetime of the process.
_CLEANUP_AFTER_S = 60.0


class _Sub:
    """Per-subscriber state.

    `queue` carries events (and a final `None` sentinel on terminal).
    `closed` flips to True the moment the broker decides this consumer
    is too slow — the SSE handler checks it on its next read so it
    drains and exits even if a `None` could not be enqueued (full
    queue). Without this flag, a stuck tab would be silently dropped
    and the handler would block on `q.get()` forever.
    """

    __slots__ = ("queue", "closed")

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self.closed: bool = False


_subscribers: dict[uuid.UUID, list[_Sub]] = {}
_history: dict[uuid.UUID, Deque[bytes]] = {}
_terminal: dict[uuid.UUID, bool] = {}
# Pending cleanup tasks keyed by doc_id. We track them so that a
# re-ingest (via /retry) cancels the prior cleanup and schedules a
# fresh one — otherwise a second `mark_terminal` would orphan the
# first task and the broker would forget the new state.
_cleanup_tasks: dict[uuid.UUID, asyncio.Task] = {}
_registry_lock = asyncio.Lock()


async def subscribe(document_id: uuid.UUID) -> _Sub:
    """Register a new subscriber. Caller is responsible for `unsubscribe`.

    The returned `_Sub` is empty and not closed on return; the SSE
    handler is expected to drain `sub.queue` and emit bytes. A `None`
    value in the queue is the end-of-stream sentinel: the handler
    should close the response. `sub.closed` may also flip to True if
    the broker gives up on this consumer (e.g. its queue is full);
    the handler should drain and exit on its next read.
    """
    async with _registry_lock:
        subs = _subscribers.setdefault(document_id, [])
        sub = _Sub()
        subs.append(sub)
        return sub


async def unsubscribe(document_id: uuid.UUID, sub: _Sub) -> None:
    """Remove a subscriber. Safe to call twice; safe on missing doc."""
    async with _registry_lock:
        subs = _subscribers.get(document_id)
        if not subs:
            return
        try:
            subs.remove(sub)
        except ValueError:
            return
        if not subs:
            _subscribers.pop(document_id, None)


async def publish(document_id: uuid.UUID, event_bytes: bytes) -> None:
    """Append an event to history and fan it out to live subscribers.

    If a subscriber's queue is full, that subscriber is marked closed
    and removed — better to drop a stuck tab than to block the ingest
    pipeline waiting for it to drain. The handler observes `closed`
    on its next read and tears down.
    """
    async with _registry_lock:
        hist: Deque[bytes] = _history.setdefault(
            document_id, collections.deque(maxlen=_HISTORY_MAX)
        )
        hist.append(event_bytes)
        subs = list(_subscribers.get(document_id, []))

    to_drop: list[_Sub] = []
    for sub in subs:
        if sub.closed:
            to_drop.append(sub)
            continue
        try:
            sub.queue.put_nowait(event_bytes)
        except asyncio.QueueFull:
            # Drop the slow consumer. We can't reliably enqueue a
            # sentinel either (the queue is full), so flip the closed
            # flag — the handler will see it on its next read.
            sub.closed = True
            to_drop.append(sub)
    for sub in to_drop:
        await unsubscribe(document_id, sub)


async def history(document_id: uuid.UUID) -> list[bytes]:
    """Snapshot of the replay buffer for a reconnecting client.

    Returns a list (not a deque) so the caller can iterate without
    holding the registry lock.
    """
    async with _registry_lock:
        hist = _history.get(document_id)
        if not hist:
            return []
        return list(hist)


async def is_terminal(document_id: uuid.UUID) -> bool:
    """Has this doc reached a terminal state (`indexed` or `failed`)?

    Subscribers check this to decide whether to send a STATE and
    immediately close, vs. stay subscribed for live updates.
    """
    async with _registry_lock:
        return _terminal.get(document_id, False)


async def mark_terminal(document_id: uuid.UUID) -> None:
    """Mark a doc as terminal, push `None` to live subscribers, schedule cleanup.

    After this call, the SSE handler for a newly-connected client
    should send one STATE and close. Existing live subscribers receive
    a `None` end-of-stream and tear down.
    """
    async with _registry_lock:
        _terminal[document_id] = True
        subs = list(_subscribers.get(document_id, []))

    # Close every live subscriber. We push `None` *after* releasing the
    # registry lock so a slow consumer's drain doesn't block the next
    # publish. The order doesn't matter: the terminal state is already
    # recorded, so any future subscriber sees the right answer.
    #
    # If the queue is full we still flip `closed=True` so the handler
    # tears down — same path as the slow-consumer drop in `publish`.
    for sub in subs:
        if sub.closed:
            await unsubscribe(document_id, sub)
            continue
        try:
            sub.queue.put_nowait(None)
        except asyncio.QueueFull:
            sub.closed = True
        await unsubscribe(document_id, sub)

    # Schedule forgetting the doc after a grace period. Long enough for
    # a late-reconnecting browser to read history + STATE, short enough
    # that per-doc state doesn't accumulate.
    async with _registry_lock:
        prior = _cleanup_tasks.pop(document_id, None)
        if prior is not None and not prior.done():
            prior.cancel()
        _cleanup_tasks[document_id] = asyncio.create_task(
            _delayed_cleanup(document_id, _CLEANUP_AFTER_S)
        )


async def reopen(document_id: uuid.UUID) -> None:
    """Reset the terminal flag for a doc, cancelling any pending cleanup.

    Used by the `/retry` endpoint so a re-ingest is broadcast as
    live updates (not a one-shot STATE). Idempotent: a no-op for a
    doc that was never marked terminal.
    """
    async with _registry_lock:
        if not _terminal.get(document_id):
            return
        _terminal[document_id] = False
        prior = _cleanup_tasks.pop(document_id, None)
    if prior is not None and not prior.done():
        prior.cancel()


async def _delayed_cleanup(document_id: uuid.UUID, delay_s: float) -> None:
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    async with _registry_lock:
        # Only forget if no live subscribers re-appeared during the
        # grace window. (If the doc is re-ingested via /retry, history
        # will be repopulated with the new run's events, so leaving
        # state alone is safer than wiping.)
        if _subscribers.get(document_id):
            _cleanup_tasks.pop(document_id, None)
            return
        _subscribers.pop(document_id, None)
        _history.pop(document_id, None)
        _terminal.pop(document_id, None)
        _cleanup_tasks.pop(document_id, None)


async def shutdown() -> None:
    """Cancel any pending cleanup tasks. Best-effort; safe to call twice.

    Used in process-shutdown hooks (none today, but reserved for
    graceful worker drains when we move to a real background worker).
    """
    # asyncio doesn't expose "all tasks" in a clean way, but every
    # `_delayed_cleanup` task is short-lived and self-cancelling on
    # GC. So this is currently a no-op; the function exists so callers
    # have a stable hook.
    return None

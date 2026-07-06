"""Unit tests for the in-process event broker.

Covers the wire-format contract that the SSE handler relies on:
  - subscribe → publish fan-out
  - history ring buffer
  - terminal sentinel + cleanup
  - slow consumer gets dropped, not backpressured
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.services.ingestion import events


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _cancel_pending_cleanup_tasks():
    """After each test, cancel any cleanup tasks `mark_terminal` spawned.

    Without this, asyncio emits "Task was destroyed but it is pending"
    warnings between tests because the 60s cleanup sleeps outlive the
    test's event-loop scope.
    """
    yield
    for task in list(events._cleanup_tasks.values()):
        if not task.done():
            task.cancel()
    # Drain cancellations so we don't leave dangling tasks.
    for task in list(events._cleanup_tasks.values()):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    events._cleanup_tasks.clear()


async def test_subscribe_and_publish_fans_out():
    doc_id = uuid.uuid4()
    s1 = await events.subscribe(doc_id)
    s2 = await events.subscribe(doc_id)

    await events.publish(doc_id, b"data: one\n\n")
    await events.publish(doc_id, b"data: two\n\n")

    assert await s1.queue.get() == b"data: one\n\n"
    assert await s1.queue.get() == b"data: two\n\n"
    assert await s2.queue.get() == b"data: one\n\n"
    assert await s2.queue.get() == b"data: two\n\n"


async def test_publish_appends_to_history_in_order():
    doc_id = uuid.uuid4()
    for i in range(3):
        await events.publish(doc_id, f"data: e{i}\n\n".encode())
    hist = await events.history(doc_id)
    assert hist == [f"data: e{i}\n\n".encode() for i in range(3)]


async def test_history_is_bounded():
    doc_id = uuid.uuid4()
    # Push more than the ring-buffer size; older events get evicted.
    for i in range(events._HISTORY_MAX + 5):
        await events.publish(doc_id, f"data: e{i}\n\n".encode())
    hist = await events.history(doc_id)
    assert len(hist) == events._HISTORY_MAX
    # Newest is at the end; oldest kept is the (5th-from-last) push.
    assert hist[-1] == f"data: e{events._HISTORY_MAX + 4}\n\n".encode()
    assert hist[0] == f"data: e{5}\n\n".encode()


async def test_terminal_marks_state_and_sentinelizes_subscribers():
    doc_id = uuid.uuid4()
    sub = await events.subscribe(doc_id)
    await events.publish(doc_id, b"data: pre\n\n")
    assert await events.is_terminal(doc_id) is False

    await events.mark_terminal(doc_id)
    assert await events.is_terminal(doc_id) is True
    # Drain the pre-event then assert the sentinel arrived.
    assert await sub.queue.get() == b"data: pre\n\n"
    assert await sub.queue.get() is None


async def test_slow_consumer_is_dropped_not_backpressured():
    """A subscriber that never reads should NOT block publishes.

    We stuff the queue past its maxsize, then verify that subsequent
    publishes still complete quickly (no infinite wait) and that the
    stuck subscriber is marked `closed` and removed from the
    subscribers list (so the broker stops fanning out to it).
    """
    doc_id = uuid.uuid4()
    stuck = await events.subscribe(doc_id)
    # Fill the bounded queue without draining it.
    for i in range(events._QUEUE_MAX + 5):
        try:
            stuck.queue.put_nowait(f"data: e{i}\n\n".encode())
        except asyncio.QueueFull:
            break

    # The next publish should still complete: it must not block waiting
    # for `stuck` to drain. We use `wait_for` as a watchdog: if the
    # broker is broken, this raises TimeoutError.
    await asyncio.wait_for(
        events.publish(doc_id, b"data: live\n\n"), timeout=0.5
    )

    # The broker should have flipped `closed` and unsubscribed `stuck`.
    assert stuck.closed is True
    # A second publish to the same doc must still work and not touch
    # `stuck` (it's no longer a subscriber).
    await events.publish(doc_id, b"data: live2\n\n")


async def test_unsubscribe_is_idempotent():
    doc_id = uuid.uuid4()
    sub = await events.subscribe(doc_id)
    await events.unsubscribe(doc_id, sub)
    # Second call is a no-op, not an error.
    await events.unsubscribe(doc_id, sub)
    # No subscribers left; publishes still succeed.
    await events.publish(doc_id, b"data: orphan\n\n")
    assert await events.history(doc_id) == [b"data: orphan\n\n"]


async def test_cleanup_forgets_doc_after_grace():
    """After mark_terminal + grace, per-doc state is gone.

    We temporarily shrink the grace so the test stays fast.
    """
    doc_id = uuid.uuid4()
    await events.publish(doc_id, b"data: x\n\n")
    await events.mark_terminal(doc_id)
    # mark_terminal schedules a cleanup with `_CLEANUP_AFTER_S` (60s).
    # We don't want to wait that long in a test; instead, we manually
    # invoke the cleanup coroutine to assert its semantics.
    await events._delayed_cleanup(doc_id, 0)
    assert await events.history(doc_id) == []
    assert await events.is_terminal(doc_id) is False
    # And the doc is gone — a fresh subscribe gives a queue that
    # nothing else will publish to.
    sub = await events.subscribe(doc_id)
    assert sub.queue.empty()

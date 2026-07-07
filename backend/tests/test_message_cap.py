"""Regression tests for H-13 — pre-filter user message and history.

The user message and history are both bounded at the agent boundary
to prevent a single turn from blowing the LLM's context window. The
schema-level cap on ``ChatRequest.message`` is 8000 chars; the
agent-level cap is tighter (4000) so a malicious or buggy caller
cannot exhaust the LLM context even when combined with the full
history.
"""
from __future__ import annotations


def test_short_message_unchanged():
    from app.services.orchestrator.agent import _cap_message

    msg, overflow = _cap_message("hello world")
    assert msg == "hello world"
    assert overflow is False


def test_empty_message_handled():
    from app.services.orchestrator.agent import _cap_message

    msg, overflow = _cap_message("")
    assert msg == ""
    assert overflow is False


def test_long_message_truncated_to_cap():
    from app.services.orchestrator.agent import _cap_message, _MAX_USER_MESSAGE_CHARS

    long_msg = "a" * (_MAX_USER_MESSAGE_CHARS + 1000)
    msg, overflow = _cap_message(long_msg)
    assert len(msg) == _MAX_USER_MESSAGE_CHARS
    assert overflow is True


def test_message_at_cap_not_truncated():
    from app.services.orchestrator.agent import _cap_message, _MAX_USER_MESSAGE_CHARS

    msg, overflow = _cap_message("a" * _MAX_USER_MESSAGE_CHARS)
    assert len(msg) == _MAX_USER_MESSAGE_CHARS
    assert overflow is False


def test_history_list_capped_to_max():
    from app.services.orchestrator.agent import _cap_history, _MAX_HISTORY_MESSAGES

    history = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
    out = _cap_history(history)
    assert len(out) == _MAX_HISTORY_MESSAGES
    # Most recent messages kept
    assert out[-1]["content"] == "msg19"


def test_history_per_message_capped():
    from app.services.orchestrator.agent import _cap_history, _MAX_HISTORY_MESSAGE_CHARS

    history = [
        {"role": "user", "content": "x" * (_MAX_HISTORY_MESSAGE_CHARS + 500)}
    ]
    out = _cap_history(history)
    assert len(out[0]["content"]) == _MAX_HISTORY_MESSAGE_CHARS


def test_history_preserves_role():
    from app.services.orchestrator.agent import _cap_history

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    out = _cap_history(history)
    assert out[0]["role"] == "user"
    assert out[1]["role"] == "assistant"


def test_empty_history_unchanged():
    from app.services.orchestrator.agent import _cap_history

    out = _cap_history([])
    assert out == []

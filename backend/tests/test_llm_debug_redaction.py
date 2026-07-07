"""Regression tests for M-31 — PII redaction in llm.debug logs.

The ``llm.debug.prompt`` and ``llm.debug.response`` log lines must
NOT contain the raw prompt or response. They must contain a
*structural fingerprint* of the prompt (count, total chars, role
distribution, first user char count) and a *structural fingerprint*
of the response (char count, tool call presence, model name, usage
numbers).

A user message can contain email addresses, names, API keys, or the
contents of an uploaded document; logging the raw text is a
data-handling hazard that the orchestrator used to commit. The
fingerprint helpers below are the only thing the debug loggers are
allowed to call.

We assert by calling the helpers directly (no LLM in the loop) and
also by calling ``LLMClient.complete`` against a stub adapter and
capturing the logger output.
"""
from __future__ import annotations

import io
import logging

import pytest


# ---------------------------------------------------------------------------
# _fingerprint_messages — direct unit tests
# ---------------------------------------------------------------------------

def test_fingerprint_messages_empty():
    from app.services.orchestrator.llm_client import _fingerprint_messages

    assert _fingerprint_messages([]) == {
        "count": 0,
        "total_chars": 0,
        "roles": {},
        "has_tools": False,
        "first_user_chars": 0,
    }


def test_fingerprint_messages_not_a_list():
    from app.services.orchestrator.llm_client import _fingerprint_messages

    # Defensive: callers should always pass a list, but if they
    # pass None or a string, return the empty shape instead of
    # raising.
    assert _fingerprint_messages(None) == {
        "count": 0,
        "total_chars": 0,
        "roles": {},
        "has_tools": False,
        "first_user_chars": 0,
    }
    assert _fingerprint_messages("oops") == {
        "count": 0,
        "total_chars": 0,
        "roles": {},
        "has_tools": False,
        "first_user_chars": 0,
    }


def test_fingerprint_messages_typical_prompt():
    from app.services.orchestrator.llm_client import _fingerprint_messages

    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "Paris."},
    ]
    fp = _fingerprint_messages(msgs)
    assert fp["count"] == 3
    assert fp["total_chars"] == sum(
        len(m["content"]) for m in msgs
    )
    assert fp["roles"] == {"system": 1, "user": 1, "assistant": 1}
    assert fp["has_tools"] is False
    assert fp["first_user_chars"] == len("What is the capital of France?")


def test_fingerprint_messages_role_normalization():
    from app.services.orchestrator.llm_client import _fingerprint_messages

    # Mixed case + unknown role.
    msgs = [
        {"role": "SYSTEM", "content": "x"},
        {"role": "user", "content": "y"},
        {"role": "wizard", "content": "z"},
    ]
    fp = _fingerprint_messages(msgs)
    assert fp["roles"] == {"system": 1, "user": 1, "other": 1}


def test_fingerprint_messages_has_tools_when_tool_role_present():
    from app.services.orchestrator.llm_client import _fingerprint_messages

    msgs = [
        {"role": "user", "content": "search for 'cats'"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "cats — Wikipedia"},
    ]
    fp = _fingerprint_messages(msgs)
    assert fp["has_tools"] is True
    assert fp["roles"]["tool"] == 1


def test_fingerprint_messages_does_not_contain_pii():
    """The whole point: the fingerprint must never include the
    actual content of any message. We feed in a PII-laden message
    and assert that none of the PII appears in the fingerprint.
    """
    from app.services.orchestrator.llm_client import _fingerprint_messages

    pii_email = "alice@example.com"
    pii_phone = "+1-555-867-5309"
    pii_key = "sk-live-abc123def456ghi789jkl"
    msgs = [
        {
            "role": "user",
            "content": f"Email me at {pii_email} or call {pii_phone}. Key: {pii_key}.",
        }
    ]
    fp = _fingerprint_messages(msgs)
    flat = repr(fp)
    assert pii_email not in flat
    assert pii_phone not in flat
    assert pii_key not in flat


def test_fingerprint_messages_handles_list_content():
    """OpenAI-style multimodal messages use a list of content parts."""
    from app.services.orchestrator.llm_client import _fingerprint_messages

    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/secret-internal-url-with-PII"}},
            ],
        }
    ]
    fp = _fingerprint_messages(msgs)
    assert fp["count"] == 1
    assert fp["roles"] == {"user": 1}
    assert fp["total_chars"] == len("What is in this image?")
    # The image URL must not appear anywhere in the fingerprint.
    assert "secret-internal-url-with-PII" not in repr(fp)


def test_fingerprint_messages_handles_non_dict_message():
    from app.services.orchestrator.llm_client import _fingerprint_messages

    # Defensive: if a caller pushes a non-dict into the list (it
    # shouldn't, but if it does), we still produce a fingerprint.
    msgs = [
        "garbage",
        {"role": "user", "content": "hi"},
        42,
    ]
    fp = _fingerprint_messages(msgs)
    assert fp["count"] == 3
    assert fp["roles"] == {"other": 2, "user": 1}
    assert fp["total_chars"] == 2  # just "hi"


# ---------------------------------------------------------------------------
# _fingerprint_response — direct unit tests
# ---------------------------------------------------------------------------

class _FakePalResp:
    """Stand-in for the PAL's LLMResponse dataclass."""
    def __init__(self, text="", tool_call=None, usage=None, model=""):
        self.text = text
        self.tool_call = tool_call
        self.usage = usage or {}
        self.model = model


def test_fingerprint_response_minimal():
    from app.services.orchestrator.llm_client import _fingerprint_response

    fp = _fingerprint_response(_FakePalResp(text="Hello."))
    assert fp == {
        "chars": 6,
        "has_tool_call": False,
        "tool_name": "",
        "model": "",
        "usage": {},
    }


def test_fingerprint_response_with_tool_call():
    from app.services.orchestrator.llm_client import _fingerprint_response

    fp = _fingerprint_response(
        _FakePalResp(
            text="",
            tool_call={"name": "search_docs", "arguments": {"q": "cats"}},
            model="qwen2.5:1.5b-instruct",
        )
    )
    assert fp["chars"] == 0
    assert fp["has_tool_call"] is True
    assert fp["tool_name"] == "search_docs"
    assert fp["model"] == "qwen2.5:1.5b-instruct"


def test_fingerprint_response_with_usage():
    from app.services.orchestrator.llm_client import _fingerprint_response

    fp = _fingerprint_response(
        _FakePalResp(
            text="Some answer.",
            usage={"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        )
    )
    assert fp["chars"] == 12
    assert fp["usage"] == {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}


def test_fingerprint_response_filters_non_numeric_usage():
    from app.services.orchestrator.llm_client import _fingerprint_response

    fp = _fingerprint_response(
        _FakePalResp(
            text="x",
            usage={"prompt_tokens": 5, "garbage": "not a number", "nested": {"x": 1}},
        )
    )
    # Only the numeric scalar is kept; nested dicts are dropped.
    assert fp["usage"] == {"prompt_tokens": 5}


def test_fingerprint_response_does_not_contain_response_text():
    from app.services.orchestrator.llm_client import _fingerprint_response

    secret = "My SSN is 123-45-6789."
    fp = _fingerprint_response(_FakePalResp(text=secret))
    assert secret not in repr(fp)
    assert fp["chars"] == len(secret)


def test_fingerprint_response_handles_non_string_text():
    from app.services.orchestrator.llm_client import _fingerprint_response

    # Defensive: if a non-string slips into text, we should not raise.
    fp = _fingerprint_response(_FakePalResp(text=None))
    assert fp["chars"] == 0


# ---------------------------------------------------------------------------
# End-to-end: the call sites in LLMClient.complete / .stream use the
# fingerprint helpers — never log the raw prompt/response.
#
# We patch the helpers themselves to record their inputs and then
# drive a real call through LLMClient with a stub adapter. This is
# the most reliable way to assert "the production code path uses
# the fingerprint" without coupling the test to structlog's
# internal logger configuration (which is rendered to stdout, not
# captured by pytest's caplog).
# ---------------------------------------------------------------------------

class _StubAdapter:
    """Minimal PAL adapter that records the request and returns a canned response."""
    name = "stub"

    def __init__(self, text="OK"):
        self._text = text
        self.last_request = None

    async def chat(self, req):
        self.last_request = req
        # Return a PAL-shaped LLMResponse. The PAL's LLMResponse
        # dataclass does not carry a ``model`` field — the
        # orchestrator tracks the model separately. Mirror the
        # real shape exactly.
        from app.services.providers import base as pal
        return pal.LLMResponse(
            text=self._text,
            tool_call=None,
            usage={"prompt_tokens": 1},
            provider="stub",
        )

    async def stream(self, req):
        yield {"delta": self._text, "done": True}
        return


class _StubRouter:
    def __init__(self, adapter):
        self._adapter = adapter
        self._cache = {}

    async def resolve(self, session=None, user_id=None, *, connector_id=None, model_hint=None, **_):
        return self._adapter, "stub", None


class _CapturingLogger:
    """Stub structlog-style logger that records every ``info`` event
    name and its kwargs so a test can assert against the payload.
    """

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def info(self, event, **kwargs):
        self.events.append((event, dict(kwargs)))

    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


@pytest.mark.asyncio
async def test_complete_calls_fingerprint_helpers(monkeypatch):
    """The production code path must call ``_fingerprint_messages``
    and ``_fingerprint_response`` and must not pass the raw prompt
    or raw response text to the logger.
    """
    from app.services.orchestrator import llm_client as lc

    cap = _CapturingLogger()
    monkeypatch.setattr(lc, "log", cap)

    secret = "alice@example.com  ssn-123-45-6789  api-key=sk-live-abc123"
    adapter = _StubAdapter(text="OK")
    router = _StubRouter(adapter)

    class _NoopSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    client = lc.LLMClient(
        session=_NoopSession(),
        user_id="00000000-0000-0000-0000-000000000001",
        connector_id=None,
        model="stub",
    )
    client._router = router  # type: ignore[attr-defined]

    await client.complete(messages=[{"role": "user", "content": secret}])

    event_names = [e for e, _ in cap.events]
    assert "llm.debug.prompt" in event_names
    assert "llm.debug.response" in event_names

    # PII must not appear in any event payload.
    flat = repr(cap.events)
    assert "alice@example.com" not in flat
    assert "ssn-123-45-6789" not in flat
    assert "sk-live-abc123" not in flat

    # And the prompt fingerprint must report the message size.
    prompt_event = next(kw for e, kw in cap.events if e == "llm.debug.prompt")
    assert prompt_event["count"] == 1
    assert prompt_event["first_user_chars"] == len(secret)
    assert prompt_event["total_chars"] == len(secret)
    assert prompt_event["roles"] == {"user": 1}


@pytest.mark.asyncio
async def test_complete_does_not_log_raw_response(monkeypatch):
    """The ``llm.debug.response`` event must contain the fingerprint,
    not the raw response text the model produced.
    """
    from app.services.orchestrator import llm_client as lc

    cap = _CapturingLogger()
    monkeypatch.setattr(lc, "log", cap)

    secret_reply = "Your account number is 987654321 and your PIN is 1234."
    adapter = _StubAdapter(text=secret_reply)
    router = _StubRouter(adapter)

    class _NoopSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    client = lc.LLMClient(
        session=_NoopSession(),
        user_id="00000000-0000-0000-0000-000000000001",
        connector_id=None,
        model="stub",
    )
    client._router = router  # type: ignore[attr-defined]

    await client.complete(messages=[{"role": "user", "content": "hi"}])

    flat = repr(cap.events)
    assert "987654321" not in flat
    assert "PIN is 1234" not in flat

    resp_event = next(kw for e, kw in cap.events if e == "llm.debug.response")
    assert resp_event["chars"] == len(secret_reply)
    assert resp_event["has_tool_call"] is False


@pytest.mark.asyncio
async def test_stream_calls_fingerprint(monkeypatch):
    """M-31 also covers the streaming path."""
    from app.services.orchestrator import llm_client as lc

    cap = _CapturingLogger()
    monkeypatch.setattr(lc, "log", cap)

    secret = "secret-token-abcdef-9876"
    adapter = _StubAdapter(text="ok")
    router = _StubRouter(adapter)

    class _NoopSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    client = lc.LLMClient(
        session=_NoopSession(),
        user_id="00000000-0000-0000-0000-000000000001",
        connector_id=None,
        model="stub",
    )
    client._router = router  # type: ignore[attr-defined]

    events = []
    async for ev in client.stream(messages=[{"role": "user", "content": secret}]):
        events.append(ev)

    flat = repr(cap.events)
    assert "secret-token-abcdef-9876" not in flat
    event_names = [e for e, _ in cap.events]
    assert "llm.debug.prompt" in event_names


# ---------------------------------------------------------------------------
# Source-grep safety net: no path in the orchestrator still logs raw content.
# ---------------------------------------------------------------------------

def test_no_raw_prompt_logging_in_orchestrator():
    """Belt-and-braces: scan the source for any remaining raw
    ``log.info(..., messages=messages, ...)`` or
    ``log.info(..., text=pal_resp.text, ...)`` style calls that
    would leak content. M-31 is a refactor and a future contributor
    should not be able to silently re-introduce the leak.
    """
    import os
    import re

    orch_path = os.path.join(
        os.path.dirname(__file__), "..", "app", "services", "orchestrator"
    )
    orch_path = os.path.abspath(orch_path)

    # Patterns that would be a violation:
    bad_patterns = [
        re.compile(r"log\.(info|debug|warning)\s*\([^)]*messages\s*=\s*messages"),
        re.compile(r"log\.(info|debug|warning)\s*\([^)]*text\s*=\s*pal_resp\.text"),
        re.compile(r"log\.(info|debug|warning)\s*\([^)]*content\s*=\s*msg\.content"),
    ]
    # Whitelist lines that look like a violation but are actually
    # inside a comment or a docstring.
    whitelist = re.compile(r"^\s*#|^\s*\"\"\"|^\s*'''|^\s*\*")

    offenders: list[str] = []
    for root, _, files in os.walk(orch_path):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(root, fn)
            with open(full, "r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if whitelist.match(line):
                        continue
                    for pat in bad_patterns:
                        if pat.search(line):
                            offenders.append(f"{full}:{lineno}: {line.rstrip()}")
    assert not offenders, (
        "M-31 violation — raw prompt/response content is being logged:\n"
        + "\n".join(offenders)
    )

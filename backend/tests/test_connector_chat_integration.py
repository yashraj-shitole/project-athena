"""Tests for the chat-engine integration of the EMC module.

The orchestrator's `LLMClient` is a thin facade over a resolved
`ProviderAdapter`. These tests exercise that seam end-to-end:

* `LLMClient.complete()` delegates to the resolved adapter and
  populates `last_*` metrics + resolved connector/model.
* `LLMClient.stream()` yields the same `{"delta", "done"}` shape
  the existing SSE consumer expects.
* Errors from the provider are propagated as `ProviderError`
  with the right `category` and `status_code`.
* The per-turn `connector_id` and `model` are exposed on the
  response, ready to be persisted on the assistant Message.

The router is tested separately in `tests/test_model_router.py`.
Here we inject a pre-built `ModelRouter` whose adapter is a
`MockTransport`-backed `OpenAICompatibleProvider` — that gives
us hermetic, deterministic responses without spinning up a
real provider or a real database.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

# Force the conftest to populate the env *before* app import.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.orchestrator.llm_client import LLMClient, LLMResponse
from app.services.providers import base as pal
from app.services.providers.openai_compat import OpenAICompatibleProvider
from app.services.providers.router import ModelRouter


# --- Test fixtures -------------------------------------------------------

def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _streaming_response(chunks: list[bytes], status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=b"".join(chunks))


def _make_router(handler) -> ModelRouter:
    """Build a router whose fallback adapter is a MockTransport.

    The fallback (Ollama) adapter is pointed at a fake URL and its
    httpx client is replaced with one backed by the given handler.
    Every test gets a fresh router + adapter.
    """
    router = ModelRouter()
    fallback = OpenAICompatibleProvider(
        base_url="https://fake-llm.example/v1",
        api_key="sk-fake",
        default_model="fake-model",
        models=["fake-model"],
        timeout_s=5.0,
    )
    fallback._client = httpx.AsyncClient(
        base_url=fallback.base_url,
        transport=httpx.MockTransport(handler),
    )
    # Replace the router's internal cache with our pre-built adapter
    # for the (None, "fake-model") default-fallback slot. The cache
    # shape is `{connector_id: adapter}`, so we can't inject directly
    # for None — instead we override `_ollama_fallback` to return ours.
    async def _patched_fallback():
        return fallback
    router._ollama_fallback = _patched_fallback  # type: ignore[method-assign]
    return router, fallback


class _StubSession:
    """AsyncSession stand-in. Resolves `add()` calls into a list and
    tracks `commit()`. We never need `execute()` for the LLMClient
    tests — the router needs it, and we replace the fallback so the
    router never hits the stub session."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits: int = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj: Any) -> None:
        return None

    async def execute(self, _stmt: Any):
        return _StubResult()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _StubResult:
    """Stub for `session.execute(stmt)....` chain used by the router.

    `scalar_one_or_none()` is what the router calls when looking up
    user/system defaults. Returning `None` here is what makes the
    router fall through to the Ollama fallback — exactly the
    behaviour the chat-integration tests want to exercise.
    """

    def all(self):
        return []

    def one(self):
        return (0, 0, 0, 0.0, 0)

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def first(self):
        return None


@pytest_asyncio.fixture
async def session():
    return _StubSession()


# --- complete() delegation ----------------------------------------------

@pytest.mark.asyncio
async def test_complete_delegates_to_adapter_and_populates_metrics(session):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _json_response(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "hello"}}
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }
        )

    router, fallback = _make_router(handler)
    client = LLMClient(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        model="fake-model",  # route to the patched adapter
        router=router,
    )

    resp = await client.complete(
        messages=[{"role": "user", "content": "hi"}],
    )

    assert isinstance(resp, LLMResponse)
    assert resp.text == "hello"
    assert resp.tool_call is None
    assert resp.usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert resp.provider == "openai_compat"
    # The Ollama fallback path returns None for the resolved connector
    # id, which is how the agent knows "this was the built-in fallback,
    # not a user-registered connector".
    assert resp.connector_id is None
    assert resp.model == "fake-model"
    # The wire request was correct.
    assert len(calls) == 1
    assert calls[0].url.path.endswith("/chat/completions")
    assert calls[0].headers.get("Authorization") == "Bearer sk-fake"
    # Metrics were populated.
    assert client.last_provider == "openai_compat"
    assert client.last_latency_ms >= 0
    assert client.last_usage["prompt_tokens"] == 7
    await fallback.aclose()


@pytest.mark.asyncio
async def test_complete_propagates_provider_error_with_category(session):
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    router, fallback = _make_router(handler)
    client = LLMClient(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        router=router,
    )

    with pytest.raises(pal.ProviderError) as exc:
        await client.complete(messages=[{"role": "user", "content": "x"}])
    assert exc.value.category == pal.CAT_RATE_LIMIT
    assert exc.value.status_code == 429
    # The error is still recorded on the client so the agent can
    # read it post-failure.
    assert client.last_latency_ms >= 0
    await fallback.aclose()


@pytest.mark.asyncio
async def test_complete_normalizes_tool_call(session):
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"q": "athena"}',
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        )

    router, fallback = _make_router(handler)
    client = LLMClient(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        router=router,
    )
    resp = await client.complete(messages=[{"role": "user", "content": "find"}])
    assert resp.tool_call == {"name": "search", "arguments": {"q": "athena"}}
    await fallback.aclose()


# --- stream() delegation -------------------------------------------------

@pytest.mark.asyncio
async def test_stream_yields_sse_deltas_then_done(session):
    sse = (
        b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        return _streaming_response([sse])

    router, fallback = _make_router(handler)
    client = LLMClient(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        router=router,
    )
    events = []
    async for ev in client.stream(messages=[{"role": "user", "content": "x"}]):
        events.append(ev)
    text = "".join(e.get("delta", "") for e in events)
    assert text == "hello"
    # Last event is `done=True`.
    assert events[-1]["done"] is True
    await fallback.aclose()


@pytest.mark.asyncio
async def test_stream_emits_error_event_on_provider_failure(session):
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model gone")

    router, fallback = _make_router(handler)
    client = LLMClient(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        router=router,
    )
    events = []
    async for ev in client.stream(messages=[{"role": "user", "content": "x"}]):
        events.append(ev)
    assert len(events) == 1
    assert events[0]["done"] is True
    assert "error" in events[0]
    assert "404" in events[0]["error"]
    await fallback.aclose()


# --- Resolved-connector / model exposure --------------------------------

@pytest.mark.asyncio
async def test_resolved_connector_and_model_set_after_first_call(session):
    """After complete(), the client exposes the resolved connector
    and model so the agent can persist them on the Message."""

    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response({"choices": [{"message": {"content": "ok"}}]})

    router, fallback = _make_router(handler)
    client = LLMClient(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        model="my-model",
        router=router,
    )
    # Before the first call, nothing is resolved.
    assert client.resolved_model == ""
    assert client.resolved_connector_id is None

    await client.complete(messages=[{"role": "user", "content": "x"}])

    # The explicit `model="my-model"` from the request is what the
    # agent should persist; `resolved_model` is the model the router
    # resolved to (which may differ when the request doesn't pin one).
    assert client.resolved_model == "my-model"
    # Ollama fallback has no `connector_id` — the agent skips the
    # usage row for built-in traffic.
    assert client.resolved_connector_id is None
    await fallback.aclose()


@pytest.mark.asyncio
async def test_explicit_model_overrides_router_default(session):
    """If the user passes `model="gpt-4o"` and the connector's
    default is something else, the request goes out with the
    explicit model and the response surfaces that model."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _json_response(
            {"choices": [{"message": {"content": "ok"}}]}
        ) if body.get("model") == "gpt-4o" else httpx.Response(
            400, text=f"wrong model: {body.get('model')}"
        )

    router, fallback = _make_router(handler)
    client = LLMClient(
        session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        model="gpt-4o",
        router=router,
    )
    resp = await client.complete(messages=[{"role": "user", "content": "x"}])
    assert resp.model == "gpt-4o"
    assert resp.text == "ok"
    await fallback.aclose()


# --- Backwards-compat shim -----------------------------------------------

def test_get_llm_is_removed_with_clear_error():
    """`get_llm()` was the pre-EMC singleton accessor. We keep a
    stub that raises with a clear migration message."""
    from app.services.orchestrator import llm_client

    assert hasattr(llm_client, "get_llm")
    with pytest.raises(RuntimeError) as exc:
        llm_client.get_llm()
    assert "EMC" in str(exc.value)
    assert "LLMClient" in str(exc.value)

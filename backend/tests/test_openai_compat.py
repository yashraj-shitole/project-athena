"""Tests for the OpenAI-compat provider adapter.

Uses `httpx.MockTransport` so the suite stays hermetic — no network,
no real provider, no API key.

We exercise:

* Non-streaming `chat()` (text, tool call, error categories).
* Streaming `stream()` (SSE parsing, [DONE] terminator).
* `list_models()` and `health_check()` happy paths.
* `health_check()` failure paths with each error category.
* Auth-header variants (bearer, custom header, basic, none).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.providers import base
from app.services.providers.openai_compat import OpenAICompatibleProvider


# --- Test fixtures -------------------------------------------------------

def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _text_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text)


def _streaming_response(chunks: list[bytes], status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=b"".join(chunks))


def _make_provider(handler) -> OpenAICompatibleProvider:
    """Build a provider whose `httpx.AsyncClient` uses a MockTransport."""
    prov = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        default_model="test-model",
        models=["test-model", "other"],
    )
    prov._client = httpx.AsyncClient(
        base_url=prov.base_url,
        transport=httpx.MockTransport(handler),
    )
    return prov


# --- Non-streaming chat() -----------------------------------------------

@pytest.mark.asyncio
async def test_chat_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        # base_url ends in /v1, so the absolute path is /v1/chat/completions.
        assert request.url.path.endswith("/chat/completions")
        # Confirm the bearer token we set reached the wire.
        assert request.headers.get("Authorization") == "Bearer sk-test"
        return _json_response(
            {
                "id": "cmpl-1",
                "choices": [
                    {"message": {"role": "assistant", "content": "hello world"}}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }
        )

    prov = _make_provider(handler)
    req = base.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    resp = await prov.chat(req)
    assert resp.text == "hello world"
    assert resp.tool_call is None
    assert resp.usage["prompt_tokens"] == 5
    assert resp.usage["completion_tokens"] == 2
    assert resp.provider == "openai_compat"
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_normalizes_tool_call():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"q": "athena"}',
                                    }
                                }
                            ],
                        }
                    }
                ]
            }
        )

    prov = _make_provider(handler)
    req = base.ChatRequest(messages=[{"role": "user", "content": "search"}])
    resp = await prov.chat(req)
    assert resp.tool_call == {"name": "search", "arguments": {"q": "athena"}}
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_handles_string_arguments_with_bad_json():
    """Some providers ship the args as a JSON-as-text string. We
    fall back to wrapping it in a `_raw` envelope so the orchestrator
    gets something it can still pass to the tool layer."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "x", "arguments": "{not-json}"}}
                            ]
                        }
                    }
                ]
            }
        )

    prov = _make_provider(handler)
    resp = await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert resp.tool_call is not None
    assert resp.tool_call["name"] == "x"
    assert resp.tool_call["arguments"] == {"_raw": "{not-json}"}
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_missing_model_raises_bad_request():
    prov = _make_provider(lambda _r: _json_response({}))
    prov.default_model = ""
    prov.models = []
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_BAD_REQUEST
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_empty_messages_raises_bad_request():
    prov = _make_provider(lambda _r: _json_response({}))
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[]))
    assert exc.value.category == base.CAT_BAD_REQUEST
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_categorizes_401_as_auth_failed():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _text_response("unauthorized", status=401)

    prov = _make_provider(handler)
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_AUTH
    assert exc.value.status_code == 401
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_categorizes_429_as_rate_limited():
    prov = _make_provider(lambda _r: _text_response("slow down", status=429))
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_RATE_LIMIT
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_categorizes_500_as_server_error():
    prov = _make_provider(lambda _r: _text_response("boom", status=500))
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_SERVER
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_categorizes_404_as_not_found():
    prov = _make_provider(lambda _r: _text_response("model not found", status=404))
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_NOT_FOUND
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_invalid_json_response_raises_invalid_response():
    prov = _make_provider(lambda _r: _text_response("not-json", status=200))
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_INVALID_RESPONSE
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_timeout_raises_timeout():
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("nope")

    prov = _make_provider(handler)
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_TIMEOUT
    await prov.aclose()


@pytest.mark.asyncio
async def test_chat_network_error_raises_network():
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    prov = _make_provider(handler)
    with pytest.raises(base.ProviderError) as exc:
        await prov.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_NETWORK
    await prov.aclose()


# --- Streaming chat() ---------------------------------------------------

@pytest.mark.asyncio
async def test_stream_parses_sse_chunks_and_done_terminator():
    sse = (
        b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        return _streaming_response([sse], status=200)

    prov = _make_provider(handler)
    events = []
    async for ev in prov.stream(base.ChatRequest(messages=[{"role": "user", "content": "x"}])):
        events.append(ev)
    # The text arrives in two deltas; the final `done=True` event can
    # be emitted twice (once for `finish_reason: "stop"` and once for
    # the `[DONE]` sentinel) — we just check the *first* terminal
    # event and that no `error` ever appears.
    text = "".join(e["delta"] for e in events)
    assert text == "hello"
    assert events[-1]["done"] is True
    assert all("error" not in e for e in events)
    await prov.aclose()


@pytest.mark.asyncio
async def test_stream_emits_error_on_non_200():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _text_response("model gone", status=404)

    prov = _make_provider(handler)
    events = []
    async for ev in prov.stream(base.ChatRequest(messages=[{"role": "user", "content": "x"}])):
        events.append(ev)
    assert len(events) == 1
    assert events[0]["done"] is True
    assert "error" in events[0]
    assert "404" in events[0]["error"]
    await prov.aclose()


@pytest.mark.asyncio
async def test_stream_skips_non_json_lines():
    """Some providers insert `:` keepalives; the parser must not crash."""
    sse = b": keepalive\n\n" \
          b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n' \
          b"data: [DONE]\n\n"
    prov = _make_provider(lambda _r: _streaming_response([sse]))
    events = []
    async for ev in prov.stream(base.ChatRequest(messages=[{"role": "user", "content": "x"}])):
        events.append(ev)
    assert events[0]["delta"] == "ok"
    assert events[-1]["done"] is True
    await prov.aclose()


# --- Auth variants -------------------------------------------------------

def test_bearer_auth_adds_authorization_header():
    prov = OpenAICompatibleProvider(base_url="https://x", api_key="sk-abc")
    h = prov._headers()
    assert h["Authorization"] == "Bearer sk-abc"
    await_prov_cleanup(prov)


def test_header_auth_uses_custom_name():
    prov = OpenAICompatibleProvider(
        base_url="https://x", api_key="sk-abc", auth_type="header", auth_header_name="x-api-key"
    )
    h = prov._headers()
    assert h["x-api-key"] == "sk-abc"
    assert "Authorization" not in h
    await_prov_cleanup(prov)


def test_basic_auth_uses_basic_prefix():
    prov = OpenAICompatibleProvider(
        base_url="https://x", api_key="dXNlcjpwYXNz", auth_type="basic"
    )
    h = prov._headers()
    assert h["Authorization"] == "Basic dXNlcjpwYXNz"
    await_prov_cleanup(prov)


def test_no_api_key_omits_auth():
    prov = OpenAICompatibleProvider(base_url="https://x", api_key=None, auth_type="bearer")
    h = prov._headers()
    assert "Authorization" not in h
    await_prov_cleanup(prov)


def test_custom_headers_merged_with_lowest_priority():
    prov = OpenAICompatibleProvider(
        base_url="https://x",
        api_key="sk-x",
        custom_headers={"X-Trace": "abc", "X-Org": "athena"},
    )
    h = prov._headers()
    assert h["X-Trace"] == "abc"
    assert h["X-Org"] == "athena"
    assert h["Authorization"] == "Bearer sk-x"
    await_prov_cleanup(prov)


def test_organization_and_project_ids():
    prov = OpenAICompatibleProvider(
        base_url="https://x", api_key="sk-x", organization_id="org-1", project_id="proj-2"
    )
    h = prov._headers()
    assert h["OpenAI-Organization"] == "org-1"
    assert h["OpenAI-Project"] == "proj-2"
    await_prov_cleanup(prov)


def await_prov_cleanup(prov):
    """`aclose` is async; provide a tiny shim so the synchronous tests
    can still construct + close a provider."""
    import asyncio

    asyncio.get_event_loop().run_until_complete(prov.aclose())


# --- list_models() and health_check() -----------------------------------

@pytest.mark.asyncio
async def test_list_models_returns_provider_ids():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response({"data": [{"id": "gpt-4o"}, {"id": "gpt-3.5"}]})

    prov = _make_provider(handler)
    ids = await prov.list_models()
    assert ids == ["gpt-4o", "gpt-3.5"]
    await prov.aclose()


@pytest.mark.asyncio
async def test_list_models_falls_back_to_configured_on_error():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _text_response("nope", status=500)

    prov = _make_provider(handler)
    ids = await prov.list_models()
    # Returns the constructor-supplied list verbatim.
    assert ids == ["test-model", "other"]
    await prov.aclose()


@pytest.mark.asyncio
async def test_health_check_ok():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response({"choices": [{"message": {"content": "ok"}}]})

    prov = _make_provider(handler)
    report = await prov.health_check()
    assert report.ok is True
    assert report.status == "online"
    assert report.capabilities["chat"] is True
    assert report.latency_ms >= 0
    await prov.aclose()


@pytest.mark.asyncio
async def test_health_check_auth_failed():
    prov = _make_provider(lambda _r: _text_response("nope", status=401))
    report = await prov.health_check()
    assert report.ok is False
    assert report.status == "auth_failed"
    assert report.category == base.CAT_AUTH
    await prov.aclose()


@pytest.mark.asyncio
async def test_health_check_no_model_configured_is_unsupported():
    """Without a default model the probe cannot run — we report
    `unsupported` rather than spinning a 30s timeout."""
    prov = _make_provider(lambda _r: _json_response({}))
    prov.default_model = ""
    prov.models = []
    report = await prov.health_check()
    assert report.ok is False
    assert report.category == base.CAT_UNSUPPORTED
    await prov.aclose()


# --- options translation -----------------------------------------------

def test_options_translates_known_keys():
    from app.services.providers.openai_compat import _options_to_payload

    payload = _options_to_payload(
        {"temperature": 0.7, "top_p": 0.9, "max_tokens": 256}, stream=False
    )
    assert payload == {"stream": False, "temperature": 0.7, "top_p": 0.9, "max_tokens": 256}


def test_options_passes_through_unknown_keys():
    """Provider-specific knobs (`frequency_penalty`, `seed`, `stop`,
    `response_format`) flow through untouched."""
    from app.services.providers.openai_compat import _options_to_payload

    payload = _options_to_payload(
        {"frequency_penalty": 0.5, "seed": 42, "response_format": {"type": "json_object"}},
        stream=True,
    )
    assert payload["stream"] is True
    assert payload["frequency_penalty"] == 0.5
    assert payload["seed"] == 42
    assert payload["response_format"] == {"type": "json_object"}


def test_options_none_returns_minimum():
    from app.services.providers.openai_compat import _options_to_payload

    assert _options_to_payload(None, stream=False) == {"stream": False}

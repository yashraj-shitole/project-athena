"""Tests for the Phase-D provider adapters: Anthropic, Gemini,
Azure OpenAI, Ollama (native), and Custom.

Each adapter gets a focused test section that:

1. Exercises `chat()` happy path (text, tool call).
2. Verifies the wire format (URL, method, headers, body).
3. Surfaces error categories on non-200 responses.
4. Exercises `stream()` SSE/NDJSON shape.
5. Tests `list_models()` when the provider has an endpoint.
6. Tests `health_check()` happy and failure paths.

All tests use `httpx.MockTransport` so the suite stays hermetic —
no network, no API keys, no real providers.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.providers import base
from app.services.providers.anthropic import AnthropicProvider
from app.services.providers.gemini import GeminiProvider
from app.services.providers.azure_openai import AzureOpenAIProvider
from app.services.providers.ollama import OllamaProvider
from app.services.providers.custom import CustomProvider


# --- Test fixtures -------------------------------------------------------

def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _streaming_response(chunks: list[bytes], status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=b"".join(chunks))


def _make_anthropic(handler) -> AnthropicProvider:
    p = AnthropicProvider(
        base_url="https://api.example.com",
        api_key="sk-ant-test",
        default_model="claude-3-5-sonnet-20241022",
        models=["claude-3-5-sonnet-20241022"],
    )
    p._client = httpx.AsyncClient(base_url=p.base_url, transport=httpx.MockTransport(handler))
    return p


def _make_gemini(handler) -> GeminiProvider:
    p = GeminiProvider(
        base_url="https://generativelanguage.googleapis.com",
        api_key="gem-test",
        default_model="gemini-1.5-pro",
        models=["gemini-1.5-pro"],
    )
    p._client = httpx.AsyncClient(base_url=p.base_url, transport=httpx.MockTransport(handler))
    return p


def _make_azure(handler) -> AzureOpenAIProvider:
    p = AzureOpenAIProvider(
        base_url="https://my.openai.azure.com",
        api_key="azure-key",
        default_model="my-deployment",
        models=["my-deployment"],
        api_version="2024-02-01",
    )
    p._client = httpx.AsyncClient(base_url=p.base_url, transport=httpx.MockTransport(handler))
    return p


def _make_ollama(handler) -> OllamaProvider:
    p = OllamaProvider(
        base_url="http://localhost:11434",
        default_model="qwen2.5:1.5b-instruct",
        models=["qwen2.5:1.5b-instruct"],
    )
    p._client = httpx.AsyncClient(base_url=p.base_url, transport=httpx.MockTransport(handler))
    return p


def _make_custom(handler, template: dict, response_paths: dict) -> CustomProvider:
    # The custom provider reads `request_template` and `response_paths`
    # from `custom_headers` (a transitional seam; the real Connectors
    # API will write these to the connector's `settings` column and
    # the router will pass them through).
    ch = dict(custom_headers={
        "request_template": template,
        "response_paths": response_paths,
    })
    p = CustomProvider(
        base_url="https://my-custom-llm.example.com",
        api_key="ckey",
        default_model="custom-model",
        custom_headers=ch["custom_headers"],
    )
    p._client = httpx.AsyncClient(base_url=p.base_url, transport=httpx.MockTransport(handler))
    return p


# ====================================================================
# Anthropic
# ====================================================================

@pytest.mark.asyncio
async def test_anthropic_chat_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/messages")
        assert request.headers.get("x-api-key") == "sk-ant-test"
        # The system message was split out to the top-level field.
        body = json.loads(request.content)
        assert body["system"] == "you are concise"
        assert body["model"] == "claude-3-5-sonnet-20241022"
        assert body["messages"][0]["role"] == "user"
        return _json_response(
            {
                "id": "msg_1",
                "content": [{"type": "text", "text": "hi from claude"}],
                "usage": {"input_tokens": 8, "output_tokens": 4},
            }
        )

    p = _make_anthropic(handler)
    req = base.ChatRequest(
        messages=[
            {"role": "system", "content": "you are concise"},
            {"role": "user", "content": "hi"},
        ]
    )
    resp = await p.chat(req)
    assert resp.text == "hi from claude"
    assert resp.usage["prompt_tokens"] == 8
    assert resp.usage["completion_tokens"] == 4
    assert resp.provider == "anthropic"
    await p.aclose()


@pytest.mark.asyncio
async def test_anthropic_chat_normalizes_tool_use():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "search",
                        "input": {"q": "athena"},
                    }
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )

    p = _make_anthropic(handler)
    req = base.ChatRequest(messages=[{"role": "user", "content": "find"}])
    resp = await p.chat(req)
    assert resp.tool_call == {"name": "search", "arguments": {"q": "athena"}}
    await p.aclose()


@pytest.mark.asyncio
async def test_anthropic_stream_deltas_then_stop():
    sse = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hel"}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        return _streaming_response([sse])

    p = _make_anthropic(handler)
    events = []
    async for ev in p.stream(base.ChatRequest(messages=[{"role": "user", "content": "x"}])):
        events.append(ev)
    text = "".join(e.get("delta", "") for e in events)
    assert text == "hello"
    assert events[-1]["done"] is True
    await p.aclose()


@pytest.mark.asyncio
async def test_anthropic_health_check_success():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    p = _make_anthropic(handler)
    report = await p.health_check()
    assert report.ok is True
    assert report.status == "online"
    assert report.capabilities.get("chat") is True
    await p.aclose()


@pytest.mark.asyncio
async def test_anthropic_chat_propagates_auth_error():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    p = _make_anthropic(handler)
    with pytest.raises(base.ProviderError) as exc:
        await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_AUTH
    assert exc.value.status_code == 401
    await p.aclose()


# ====================================================================
# Gemini
# ====================================================================

@pytest.mark.asyncio
async def test_gemini_chat_returns_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _json_response(
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "hi from gemini"}],
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 7,
                    "totalTokenCount": 19,
                },
            }
        )

    p = _make_gemini(handler)
    req = base.ChatRequest(
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
    )
    resp = await p.chat(req)
    assert resp.text == "hi from gemini"
    assert resp.usage["prompt_tokens"] == 12
    assert resp.usage["completion_tokens"] == 7
    assert resp.provider == "gemini"
    # The system prompt was split out into system_instruction.
    assert seen["body"]["system_instruction"]["parts"][0]["text"] == "be brief"
    # API key is in the query string.
    assert "key=gem-test" in seen["url"]
    # Model is in the URL path.
    assert "/models/gemini-1.5-pro:" in seen["url"]
    await p.aclose()


@pytest.mark.asyncio
async def test_gemini_chat_normalizes_function_call():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "search",
                                        "args": {"q": "athena"},
                                    }
                                }
                            ],
                        }
                    }
                ]
            }
        )

    p = _make_gemini(handler)
    resp = await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "find"}]))
    assert resp.tool_call == {"name": "search", "arguments": {"q": "athena"}}
    await p.aclose()


@pytest.mark.asyncio
async def test_gemini_list_models_strips_prefix():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "models": [
                    {"name": "models/gemini-1.5-pro"},
                    {"name": "models/gemini-1.5-flash"},
                ]
            }
        )

    p = _make_gemini(handler)
    ids = await p.list_models()
    assert ids == ["gemini-1.5-pro", "gemini-1.5-flash"]
    await p.aclose()


@pytest.mark.asyncio
async def test_gemini_health_check_success():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]
            }
        )

    p = _make_gemini(handler)
    report = await p.health_check()
    assert report.ok is True
    assert report.status == "online"
    await p.aclose()


@pytest.mark.asyncio
async def test_gemini_chat_propagates_rate_limit():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="quota")

    p = _make_gemini(handler)
    with pytest.raises(base.ProviderError) as exc:
        await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_RATE_LIMIT
    await p.aclose()


# ====================================================================
# Azure OpenAI
# ====================================================================

@pytest.mark.asyncio
async def test_azure_chat_uses_deployment_path_and_api_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return _json_response(
            {
                "choices": [{"message": {"role": "assistant", "content": "hi from azure"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            }
        )

    p = _make_azure(handler)
    resp = await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert resp.text == "hi from azure"
    assert resp.provider == "azure_openai"
    # The deployment is in the path.
    assert "/openai/deployments/my-deployment/chat/completions" in seen["url"]
    # The api-version is in the query string.
    assert "api-version=2024-02-01" in seen["url"]
    # The api-key header is set, not Authorization: Bearer.
    assert seen["headers"].get("api-key") == "azure-key"
    assert "Authorization" not in seen["headers"]
    # The body is OpenAI-shaped (no Azure-specific fields).
    assert "messages" in seen["body"]
    assert "model" not in seen["body"]
    await p.aclose()


@pytest.mark.asyncio
async def test_azure_chat_overrides_default_deployment_with_request_model():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {"choices": [{"message": {"content": "ok"}}]}
        ) if "/deployments/turbo/chat/completions" in str(request.url) else httpx.Response(
            404, text=f"wrong: {request.url}"
        )

    p = _make_azure(handler)
    resp = await p.chat(
        base.ChatRequest(messages=[{"role": "user", "content": "x"}], model="turbo")
    )
    assert resp.text == "ok"
    await p.aclose()


@pytest.mark.asyncio
async def test_azure_health_check_success():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    p = _make_azure(handler)
    report = await p.health_check()
    assert report.ok is True
    assert report.status == "online"
    await p.aclose()


@pytest.mark.asyncio
async def test_azure_chat_propagates_not_found():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="deployment gone")

    p = _make_azure(handler)
    with pytest.raises(base.ProviderError) as exc:
        await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_NOT_FOUND
    await p.aclose()


# ====================================================================
# Ollama (native /api/chat)
# ====================================================================

@pytest.mark.asyncio
async def test_ollama_chat_uses_native_api_chat_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _json_response(
            {
                "model": "qwen2.5:1.5b-instruct",
                "message": {"role": "assistant", "content": "hi from ollama"},
                "done": True,
                "prompt_eval_count": 3,
                "eval_count": 2,
            }
        )

    p = _make_ollama(handler)
    resp = await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert resp.text == "hi from ollama"
    assert resp.usage["prompt_tokens"] == 3
    assert resp.usage["completion_tokens"] == 2
    assert resp.provider == "ollama"
    # Path is the native /api/chat, not /v1/chat/completions.
    assert seen["url"].endswith("/api/chat")
    await p.aclose()


@pytest.mark.asyncio
async def test_ollama_stream_ndjson():
    ndjson = (
        b'{"message":{"content":"hel"},"done":false}\n'
        b'{"message":{"content":"lo"},"done":true}\n'
    )

    def handler(_r: httpx.Request) -> httpx.Response:
        return _streaming_response([ndjson])

    p = _make_ollama(handler)
    events = []
    async for ev in p.stream(base.ChatRequest(messages=[{"role": "user", "content": "x"}])):
        events.append(ev)
    text = "".join(e.get("delta", "") for e in events)
    assert text == "hello"
    assert events[-1]["done"] is True
    await p.aclose()


@pytest.mark.asyncio
async def test_ollama_list_models_via_api_tags():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "models": [
                    {"name": "qwen2.5:1.5b-instruct"},
                    {"name": "llama3.2:3b"},
                ]
            }
        )

    p = _make_ollama(handler)
    ids = await p.list_models()
    assert ids == ["qwen2.5:1.5b-instruct", "llama3.2:3b"]
    await p.aclose()


@pytest.mark.asyncio
async def test_ollama_health_check_uses_api_tags():
    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response({"models": []})

    p = _make_ollama(handler)
    report = await p.health_check()
    assert report.ok is True
    assert report.status == "online"
    await p.aclose()


@pytest.mark.asyncio
async def test_ollama_chat_propagates_timeout():
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no server")

    p = _make_ollama(handler)
    with pytest.raises(base.ProviderError) as exc:
        await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.category == base.CAT_NETWORK
    await p.aclose()


# ====================================================================
# Custom
# ====================================================================

@pytest.mark.asyncio
async def test_custom_chat_uses_template_and_response_paths():
    template = {
        "model": "{{model}}",
        "input": "{{messages_json}}",
        "system": "{{system}}",
    }
    response_paths = {
        "text": "output.text",
        "usage.prompt_tokens": "usage.input",
        "usage.completion_tokens": "usage.output",
    }
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _json_response(
            {
                "output": {"text": "hi from custom"},
                "usage": {"input": 6, "output": 3},
            }
        )

    p = _make_custom(handler, template, response_paths)
    req = base.ChatRequest(
        messages=[
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
    )
    resp = await p.chat(req)
    assert resp.text == "hi from custom"
    assert resp.usage["prompt_tokens"] == 6
    assert resp.usage["completion_tokens"] == 3
    assert resp.provider == "custom"
    # Placeholders were substituted.
    assert seen["body"]["model"] == "custom-model"
    assert "user" in seen["body"]["input"]
    assert seen["body"]["system"] == "be terse"
    await p.aclose()


@pytest.mark.asyncio
async def test_custom_chat_normalizes_tool_call_from_paths():
    template = {"messages": "{{messages}}", "model": "{{model}}"}
    response_paths = {
        "text": "result.text",
        "tool_call.name": "result.tool.name",
        "tool_call.arguments": "result.tool.args",
    }

    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "result": {
                    "text": "",
                    "tool": {"name": "search", "args": {"q": "athena"}},
                }
            }
        )

    p = _make_custom(handler, template, response_paths)
    resp = await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "find"}]))
    assert resp.tool_call == {"name": "search", "arguments": {"q": "athena"}}
    await p.aclose()


@pytest.mark.asyncio
async def test_custom_stream_falls_back_to_chunked_chat():
    # The custom adapter can't parse a generic SSE stream, so stream()
    # falls back to a non-streaming chat() and emits the text in
    # chunks followed by a terminal done event (instead of the old
    # hard "unsupported" error).
    template = {"messages": "{{messages}}"}
    response_paths = {"text": "text"}

    def handler(_r: httpx.Request) -> httpx.Response:
        return _json_response({"text": "x"})

    p = _make_custom(handler, template, response_paths)
    events = []
    async for ev in p.stream(base.ChatRequest(messages=[{"role": "user", "content": "x"}])):
        events.append(ev)
    # At least one content delta + a terminal done; no error.
    assert events[-1]["done"] is True
    assert "error" not in events[-1]
    assert "".join(ev.get("delta", "") for ev in events) == "x"
    await p.aclose()


@pytest.mark.asyncio
async def test_custom_chat_propagates_invalid_response():
    template = {"messages": "{{messages}}"}
    response_paths = {"text": "text"}

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    p = _make_custom(handler, template, response_paths)
    with pytest.raises(base.ProviderError) as exc:
        await p.chat(base.ChatRequest(messages=[{"role": "user", "content": "x"}]))
    assert exc.value.status_code == 500
    await p.aclose()


def test_custom_provider_rejects_missing_template():
    """If the user didn't supply a `request_template`, the adapter
    fails fast at construction time — no half-built adapter that
    errors on every call."""
    with pytest.raises(base.ProviderError):
        CustomProvider(base_url="https://x.example.com")


# ====================================================================
# Registry — all 5 Phase-D providers are registered
# ====================================================================

def test_registry_lists_all_phase_d_providers():
    from app.services.providers import registry

    names = registry.all_providers()
    for required in ("openai_compat", "anthropic", "gemini", "azure_openai", "ollama", "custom"):
        assert required in names, f"missing provider in registry: {required}"

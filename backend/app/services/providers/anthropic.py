"""Anthropic Messages API adapter.

The Anthropic wire format is materially different from OpenAI's:

* `system` is a top-level field, not a `messages[0]` entry — we split
  it out before posting.
* Tools use `{name, description, input_schema}` instead of
  `{type: "function", function: {...}}` — we translate from the
  orchestrator's OpenAI-shaped tool list to Anthropic's shape.
* Tool calls come back as `content[].type == "tool_use"` blocks. We
  pick the first one for `LLMResponse.tool_call` (the orchestrator
  only handles a single tool call per turn).
* Streaming uses `event: <type>` SSE events instead of OpenAI's
  `data: {...}` JSON lines. We care about `content_block_delta`
  (`delta.type == "text_delta"`) for text and `content_block_start`
  (`content_block.type == "tool_use"`) to begin a tool call.
* The required `anthropic-version` header (default `2023-06-01`) goes
  in `custom_headers` from the connector row; the adapter forwards it
  verbatim.

The base URL is whatever the user supplies; Anthropic's hosted API
is `https://api.anthropic.com`. The path is `{base_url}/v1/messages`.

Anthropic does not expose a `list_models` endpoint; the adapter
returns the user-configured `models` list as-is.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.logging import get_logger
from app.services.providers.base import (
    CAT_AUTH,
    CAT_BAD_REQUEST,
    CAT_INVALID_RESPONSE,
    CAT_NETWORK,
    CAT_NOT_FOUND,
    CAT_OK,
    CAT_RATE_LIMIT,
    CAT_SERVER,
    CAT_TIMEOUT,
    CAT_UNSUPPORTED,
    ChatRequest,
    HealthReport,
    LLMResponse,
    ProviderAdapter,
    ProviderError,
)

log = get_logger(__name__)


_DEFAULT_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 8.0
_MAX_TOKENS_DEFAULT = 1024


def _categorize_http(status: int) -> str:
    if status in (200, 201):
        return CAT_OK
    if status in (401, 403):
        return CAT_AUTH
    if status == 404:
        return CAT_NOT_FOUND
    if status == 429:
        return CAT_RATE_LIMIT
    if 400 <= status < 500:
        return CAT_BAD_REQUEST
    if status >= 500:
        return CAT_SERVER
    return CAT_UNKNOWN


def _split_system(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
    """Pop the first `role == "system"` message out as `system`.

    Anthropic takes the system prompt as a top-level string. The
    orchestrator may also pass a system message via `options` —
    the OpenAI-compat adapter already handles that path; here we
    only translate the messages list.
    """
    out: list[dict] = []
    system: Optional[str] = None
    for m in messages:
        if m.get("role") == "system" and system is None:
            content = m.get("content")
            if isinstance(content, str):
                system = content
            continue
        out.append(m)
    return system, out


def _tools_anthropic(tools: Optional[list[dict]]) -> Optional[list[dict]]:
    """Translate OpenAI-style tool defs to Anthropic's shape.

    OpenAI: `{"type": "function", "function": {"name", "description", "parameters"}}`
    Anthropic: `{"name", "description", "input_schema"}`
    """
    if not tools:
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        if not fn:
            # Already in the right shape, or some custom config.
            out.append(t)
            continue
        out.append(
            {
                "name": fn.get("name") or t.get("name") or "",
                "description": fn.get("description") or t.get("description") or "",
                "input_schema": fn.get("parameters")
                or t.get("input_schema")
                or {"type": "object", "properties": {}},
            }
        )
    return out or None


def _options_to_payload(options: Optional[dict]) -> dict[str, Any]:
    """Translate the orchestrator's `options` to Anthropic params.

    Anthropic doesn't have `top_p` *or* `temperature` simultaneously
    recommended; if both are set the upstream rejects. We pass them
    through verbatim and let the user (or the orchestrator's defaults)
    decide which to use.
    """
    if not options:
        return {}
    direct = (
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "metadata",
    )
    out: dict[str, Any] = {}
    for k, v in options.items():
        if k in direct:
            out[k] = v
    return out


def _headers(api_key: Optional[str], custom_headers: dict[str, str]) -> dict[str, str]:
    h: dict[str, str] = {
        "Content-Type": "application/json",
        # Anthropic requires an API key header (not Bearer) and a
        # version. Both are user-overridable via `custom_headers` for
        # the version, but the key header is always `x-api-key`.
        "x-api-key": api_key or "",
    }
    # Default to the documented version if the user didn't set one.
    if "anthropic-version" not in {k.lower() for k in custom_headers}:
        h["anthropic-version"] = "2023-06-01"
    if custom_headers:
        for k, v in custom_headers.items():
            h[str(k)] = str(v)
    return h


def _parse_chat_response(data: dict[str, Any]) -> LLMResponse:
    """Normalize an Anthropic Messages response to `LLMResponse`."""
    content = data.get("content") or []
    text_parts: list[str] = []
    tool_call = None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                # First tool-use block wins; the orchestrator only
                # handles a single tool call per turn.
                if tool_call is None:
                    raw_args = block.get("input") or {}
                    args = raw_args
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {"_raw": args}
                    tool_call = {
                        "name": block.get("name") or "",
                        "arguments": args or {},
                    }
    text = "".join(text_parts)
    usage = data.get("usage") or {}
    return LLMResponse(
        text=text,
        tool_call=tool_call,
        raw=data,
        usage={
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(
                (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            ),
        },
    )


class AnthropicProvider(ProviderAdapter):
    name = "anthropic"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        auth_type: str = "bearer",  # ignored — we always use x-api-key
        auth_header_name: Optional[str] = None,  # ignored
        custom_headers: Optional[dict[str, str]] = None,
        organization_id: Optional[str] = None,  # ignored
        project_id: Optional[str] = None,  # ignored
        api_version: Optional[str] = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        default_model: str = "",
        models: Optional[list[str]] = None,
    ) -> None:
        # SSRF: enforced by the router; not here.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.custom_headers = custom_headers or {}
        self.api_version = api_version
        self.default_model = default_model
        self.models = list(models or [])
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        h = _headers(self.api_key, self.custom_headers)
        if self.api_version:
            h["anthropic-version"] = self.api_version
        return h

    def _resolve_model(self, req: ChatRequest) -> str:
        return req.model or self.default_model

    async def chat(self, req: ChatRequest) -> LLMResponse:
        if not req.messages:
            raise ProviderError("chat() called with no messages", category=CAT_BAD_REQUEST)
        model = self._resolve_model(req)
        if not model:
            raise ProviderError(
                "no model selected — set `default_model` on the connector "
                "or pass `model` in the chat request",
                category=CAT_BAD_REQUEST,
            )
        system, msgs = _split_system(list(req.messages))
        payload: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "max_tokens": _MAX_TOKENS_DEFAULT,
        }
        if system:
            payload["system"] = system
        if req.tools:
            tools = _tools_anthropic(req.tools)
            if tools:
                payload["tools"] = tools
        # Honor an explicit `max_tokens` from the orchestrator.
        opts = _options_to_payload(req.options)
        if "max_tokens" in (req.options or {}):
            payload["max_tokens"] = int(req.options["max_tokens"])
        payload.update(opts)

        try:
            r = await self._client.post(
                "/v1/messages", json=payload, headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"timeout calling {self.base_url}/v1/messages: {exc}",
                category=CAT_TIMEOUT,
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"network error calling {self.base_url}/v1/messages: {exc}",
                category=CAT_NETWORK,
                provider=self.name,
            ) from exc

        if r.status_code != 200:
            snippet = (r.text or "")[:300]
            raise ProviderError(
                f"anthropic chat failed ({r.status_code}): {snippet}",
                category=_categorize_http(r.status_code),
                status_code=r.status_code,
                provider=self.name,
            )

        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(
                f"non-JSON response: {(r.text or '')[:200]}",
                category=CAT_INVALID_RESPONSE,
                provider=self.name,
            ) from exc

        resp = _parse_chat_response(data)
        resp.provider = self.name
        return resp

    async def stream(self, req: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        if not req.messages:
            yield {"delta": "", "done": True, "error": "no messages"}
            return
        model = self._resolve_model(req)
        if not model:
            yield {"delta": "", "done": True, "error": "no model selected"}
            return
        system, msgs = _split_system(list(req.messages))
        payload: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "max_tokens": _MAX_TOKENS_DEFAULT,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if req.tools:
            tools = _tools_anthropic(req.tools)
            if tools:
                payload["tools"] = tools
        opts = _options_to_payload(req.options)
        if "max_tokens" in (req.options or {}):
            payload["max_tokens"] = int(req.options["max_tokens"])
        payload.update(opts)

        try:
            async with self._client.stream(
                "POST", "/v1/messages", json=payload, headers=self._headers()
            ) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    yield {
                        "delta": "",
                        "done": True,
                        "error": f"stream failed ({r.status_code}): {(body or b'')[:300]!r}",
                    }
                    return
                # Anthropic streams `event: <type>\ndata: {...}\n\n` SSE.
                event_type: Optional[str] = None
                async for line in r.aiter_lines():
                    if not line:
                        # Blank line separates events; reset.
                        event_type = None
                        continue
                    if line.startswith("event:"):
                        event_type = line[len("event:") :].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    line = line[len("data:") :].strip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event_type == "content_block_delta":
                        delta = (ev.get("delta") or {})
                        if delta.get("type") == "text_delta":
                            yield {"delta": delta.get("text") or "", "done": False}
                    elif event_type == "message_stop":
                        yield {"delta": "", "done": True}
                        return
                    elif event_type == "error":
                        msg = (ev.get("error") or {}).get("message") or "anthropic error"
                        yield {"delta": "", "done": True, "error": msg}
                        return
        except httpx.TimeoutException as exc:
            yield {"delta": "", "done": True, "error": f"stream timeout: {exc}"}
        except httpx.HTTPError as exc:
            yield {"delta": "", "done": True, "error": f"stream network error: {exc}"}

    async def list_models(self) -> list[str]:
        # Anthropic doesn't expose a model-list endpoint; the user
        # populates `models` on the connector row at create time.
        return list(self.models)

    async def health_check(self) -> HealthReport:
        import time

        model = self.default_model or (self.models[0] if self.models else "")
        if not model:
            return HealthReport(
                ok=False,
                status="unknown",
                capabilities={},
                error="no model configured for health probe",
                category=CAT_UNSUPPORTED,
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        t0 = time.perf_counter()
        try:
            r = await self._client.post(
                "/v1/messages",
                json=payload,
                headers=self._headers(),
                timeout=httpx.Timeout(_HEALTH_TIMEOUT_S, connect=5.0),
            )
        except httpx.TimeoutException as exc:
            return HealthReport(
                ok=False, status="timeout", error=str(exc), category=CAT_TIMEOUT
            )
        except httpx.HTTPError as exc:
            return HealthReport(
                ok=False, status="offline", error=str(exc), category=CAT_NETWORK
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code == 200:
            return HealthReport(
                ok=True,
                latency_ms=latency_ms,
                status="online" if latency_ms < 3000 else "slow",
                capabilities={"chat": True, "stream": True, "tools": True},
            )
        return HealthReport(
            ok=False,
            latency_ms=latency_ms,
            status={
                401: "auth_failed",
                403: "auth_failed",
                404: "not_found",
                429: "rate_limited",
            }.get(r.status_code, "offline"),
            error=f"{r.status_code}: {(r.text or '')[:200]}",
            category=_categorize_http(r.status_code),
            status_code=r.status_code,
        )


__all__ = ["AnthropicProvider"]

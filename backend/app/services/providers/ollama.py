"""Ollama native API adapter.

Ollama's wire format predates OpenAI-compat for `/api/chat`:

* `POST {base_url}/api/chat` with `{model, messages, stream, options: {...}}`
* Response: `{message: {role, content, tool_calls?}, done, done_reason, ...}`
* Streaming: NDJSON, one JSON object per line. Each carries a partial
  `message.content` and `done: true` on the final frame.
* `GET {base_url}/api/tags` returns `{models: [{name, ...}, ...]}` —
  the native model list (today's `OllamaClient` calls this).

This adapter is the dedicated home for users who registered Ollama as
a *connector* (i.e. they want it tracked like any other model, with
usage rows + health), AND for the router's built-in Ollama fallback.
Both paths resolve here: the registry maps `"ollama"` -> OllamaProvider
(so `ModelRouter._build_adapter` constructs it for any
`provider == "ollama"` connector), and `_ollama_fallback` constructs it
directly when no connector is configured. The earlier OpenAI-compat
shim path (POST {base_url}/chat/completions) 404'd against the default
`OLLAMA_BASE_URL` (server root, no `/v1`); the native `/api/chat`
endpoint works at the root.

The orchestrator's `OllamaClient` (`app.services.llm.ollama`) is
**not** replaced by this adapter. That client is a pre-PAL singleton
still used by the embedding service and a couple of internal paths
that don't go through `ModelRouter`.
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
    _summarize_request,
)

log = get_logger(__name__)


_DEFAULT_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 8.0


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


def _options_to_ollama(options: Optional[dict]) -> dict[str, Any]:
    """Translate the orchestrator's options to Ollama's `options` block.

    Ollama takes `temperature`, `top_p`, `top_k`, `num_ctx`,
    `num_predict` (max tokens), etc. under a nested `options` key.
    """
    if not options:
        return {}
    out: dict[str, Any] = {}
    direct = (
        "temperature",
        "top_p",
        "top_k",
        "num_ctx",
        "num_predict",
        "seed",
        "stop",
        "frequency_penalty",
        "presence_penalty",
    )
    for k, v in options.items():
        if k in direct:
            out[k] = v
    return out


def _parse_chat_response(data: dict[str, Any]) -> LLMResponse:
    message = data.get("message") or {}
    text = message.get("content") or ""
    tool_call = None
    raw_calls = message.get("tool_calls") or []
    if raw_calls:
        first = raw_calls[0] or {}
        fn = first.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
        tool_call = {
            "name": fn.get("name") or first.get("name") or "",
            "arguments": args or {},
        }
    # Ollama returns counts when the call is `done: true`:
    #   { ..., "prompt_eval_count": N, "eval_count": M }
    prompt = int(data.get("prompt_eval_count") or 0)
    completion = int(data.get("eval_count") or 0)
    return LLMResponse(
        text=text,
        tool_call=tool_call,
        raw=data,
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    )


class OllamaProvider(ProviderAdapter):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,  # unused for local Ollama; required for ollama.com cloud
        auth_type: str = "none",  # default; users with cloud set "bearer"
        auth_header_name: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        organization_id: Optional[str] = None,  # ignored
        project_id: Optional[str] = None,  # ignored
        api_version: Optional[str] = None,  # ignored
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        default_model: str = "",
        models: Optional[list[str]] = None,
    ) -> None:
        # SSRF: enforced by the router. Loopback is allowed — users
        # frequently point at http://localhost:11434.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.auth_type = auth_type
        self.auth_header_name = auth_header_name
        self.custom_headers = custom_headers or {}
        self.default_model = default_model
        self.models = list(models or [])
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # Local Ollama ignores auth, but the hosted ollama.com cloud
        # requires `Authorization: Bearer <key>`. Use the same
        # bearer/header/basic logic the OpenAI-compat adapter uses,
        # so a connector registered with `auth_type=bearer` works
        # against both targets. `custom_headers` are merged last so
        # they can override anything we set above.
        from app.services.providers.openai_compat import _build_auth_headers

        return _build_auth_headers(
            self.api_key,
            self.auth_type,
            self.auth_header_name,
            self.custom_headers,
        )

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(req.messages),
            "stream": False,
        }
        opts = _options_to_ollama(req.options)
        if opts:
            payload["options"] = opts
        if req.tools:
            # Ollama accepts OpenAI-style tool defs unchanged.
            payload["tools"] = list(req.tools)

        # Debug log: captures the URL, headers (auth redacted), model,
        # message fingerprint, and option keys. Together with the
        # `llm.debug.response` log on the orchestrator side, an operator
        # can correlate a 4xx from the upstream with the exact request
        # shape. Never logs message contents.
        try:
            log.info(
                "llm.debug.request",
                adapter=self.name,
                stream=False,
                **_summarize_request(
                    provider=self.name,
                    base_url=self.base_url,
                    endpoint="/api/chat",
                    model=model,
                    headers=self._headers(),
                    messages=req.messages,
                    tools=req.tools,
                    options=opts,
                    extra={"payload_keys": sorted(payload.keys())},
                ),
            )
        except Exception:  # noqa: BLE001
            # Logging is best-effort; a malformed payload must not
            # break the call.
            pass

        try:
            r = await self._client.post("/api/chat", json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"timeout calling {self.base_url}/api/chat: {exc}",
                category=CAT_TIMEOUT,
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"network error calling {self.base_url}/api/chat: {exc}",
                category=CAT_NETWORK,
                provider=self.name,
            ) from exc

        if r.status_code != 200:
            snippet = (r.text or "")[:300]
            raise ProviderError(
                f"ollama chat failed ({r.status_code}): {snippet}",
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
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(req.messages),
            "stream": True,
        }
        opts = _options_to_ollama(req.options)
        if opts:
            payload["options"] = opts
        if req.tools:
            payload["tools"] = list(req.tools)

        # See ``chat()`` for the rationale on this log line.
        try:
            log.info(
                "llm.debug.request",
                adapter=self.name,
                stream=True,
                **_summarize_request(
                    provider=self.name,
                    base_url=self.base_url,
                    endpoint="/api/chat",
                    model=model,
                    headers=self._headers(),
                    messages=req.messages,
                    tools=req.tools,
                    options=opts,
                    extra={"payload_keys": sorted(payload.keys())},
                ),
            )
        except Exception:  # noqa: BLE001
            pass

        try:
            async with self._client.stream(
                "POST", "/api/chat", json=payload, headers=self._headers()
            ) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    yield {
                        "delta": "",
                        "done": True,
                        "error": f"stream failed ({r.status_code}): {(body or b'')[:300]!r}",
                    }
                    return
                # NDJSON: one JSON object per line.
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = ev.get("message") or {}
                    delta = msg.get("content") or ""
                    done = bool(ev.get("done"))
                    if ev.get("error"):
                        yield {"delta": "", "done": True, "error": ev["error"]}
                        return
                    yield {"delta": delta, "done": done}
                    if done:
                        return
        except httpx.TimeoutException as exc:
            yield {"delta": "", "done": True, "error": f"stream timeout: {exc}"}
        except httpx.HTTPError as exc:
            yield {"delta": "", "done": True, "error": f"stream network error: {exc}"}

    async def list_models(self) -> list[str]:
        try:
            r = await self._client.get("/api/tags", headers=self._headers())
        except httpx.HTTPError as exc:
            log.warning(
                "provider.list_models_failed", base_url=self.base_url, error=str(exc)
            )
            return list(self.models)
        if r.status_code != 200:
            return list(self.models)
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError):
            return list(self.models)
        items = data.get("models") or []
        ids: list[str] = []
        for item in items:
            mid = item.get("name")
            if isinstance(mid, str) and mid:
                ids.append(mid)
        return ids or list(self.models)

    async def health_check(self) -> HealthReport:
        import time

        # A simple GET against /api/tags is the cheapest "is the
        # server alive" probe. We don't need to load-test the
        # chat endpoint from the health loop.
        t0 = time.perf_counter()
        try:
            r = await self._client.get(
                "/api/tags",
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


__all__ = ["OllamaProvider"]

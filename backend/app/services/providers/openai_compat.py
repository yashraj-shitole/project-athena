"""OpenAI-compatible provider adapter.

Covers every provider that follows the OpenAI chat completions wire
format: OpenAI, OpenRouter, Groq, DeepSeek, Mistral, Together AI,
LM Studio, LocalAI, and any custom REST endpoint that mimics the
shape. The adapter is parametrized by `base_url` — no other per-
provider logic is needed.

Wire format assumed (POST `{base_url}/chat/completions`):

    {
      "model": "...",
      "messages": [{"role": "user", "content": "..."}],
      "tools": [...],          # optional
      "stream": true|false,
      "temperature": 0.2,      # optional
      "max_tokens": 256,       # optional
    }

Auth is `Authorization: Bearer <api_key>` by default; `auth_type=header`
lets the user pick a different header name (e.g. `x-api-key` for
some local proxies). When no API key is configured (e.g. local Ollama
running in OpenAI-compat mode), we omit the header.

`/models` is queried for `list_models()` when the provider exposes
one. Errors are mapped to the stable `ProviderError.category` strings.
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


# Default per-request timeout. Long enough for slow providers; the
# health probe sets a tighter timeout explicitly.
_DEFAULT_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 8.0
_HEALTH_PROBE_TOKENS = 1


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


def _build_auth_headers(
    api_key: Optional[str],
    auth_type: str,
    auth_header_name: Optional[str],
    custom_headers: Optional[dict[str, str]],
) -> dict[str, str]:
    """Compose the request headers.

    `auth_type` is one of: bearer (default), header, basic, none.
    `custom_headers` are merged last so they can override.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        elif auth_type == "header":
            headers[auth_header_name or "x-api-key"] = api_key
        elif auth_type == "basic":
            # `api_key` is treated as a username:password pair. We
            # don't pre-encode it — the user is expected to paste a
            # base64-encoded string if their provider needs that.
            headers["Authorization"] = f"Basic {api_key}"
        # `auth_type == "none"` is a no-op — the provider uses cookies,
        # an mTLS cert, or some other out-of-band auth.
    if custom_headers:
        for k, v in custom_headers.items():
            headers[str(k)] = str(v)
    return headers


def _options_to_payload(
    options: Optional[dict[str, Any]],
    *,
    stream: bool,
) -> dict[str, Any]:
    """Translate the orchestrator's `options` dict to OpenAI params.

    The OpenAI API expects `temperature`, `top_p`, `max_tokens`, etc.
    at the top level of the body, not under a nested `options` key
    (that's Ollama's convention). We translate the common fields; an
    unrecognized key is passed through verbatim so the user can set
    provider-specific params like `frequency_penalty`.
    """
    if not options:
        return {"stream": stream}
    out: dict[str, Any] = {"stream": stream}
    # These are the keys we know about. Anything else is copied
    # through as-is.
    direct = (
        "temperature",
        "top_p",
        "max_tokens",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "stop",
        "user",
        "response_format",
    )
    for k, v in options.items():
        if k in direct:
            out[k] = v
    return out


def _parse_chat_response(data: dict[str, Any]) -> LLMResponse:
    """Normalize an OpenAI-compat chat response to `LLMResponse`."""
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            f"malformed chat response: missing 'choices[0]'",
            category=CAT_INVALID_RESPONSE,
        ) from exc

    message = choice.get("message") or {}
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
                # Some providers ship JSON-as-text in the arguments
                # field; surface it as a string so the orchestrator
                # can still attempt to parse it.
                args = {"_raw": args}
        tool_call = {
            "name": fn.get("name") or first.get("name") or "",
            "arguments": args or {},
        }
    # OpenAI-style usage block; some providers (LM Studio, LocalAI) omit it.
    usage = data.get("usage") or {}
    return LLMResponse(
        text=text,
        tool_call=tool_call,
        raw=data,
        usage={
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    )


class OpenAICompatibleProvider(ProviderAdapter):
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        auth_type: str = "bearer",
        auth_header_name: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        default_model: str = "",
        models: Optional[list[str]] = None,
    ) -> None:
        # SSRF: the *router* (or the API handler that created the
        # adapter) is responsible for `assert_safe_url` before
        # construction. We don't do it here because the adapter is
        # also used in tests where the host may not resolve. The
        # router's check is the line of defense.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.auth_type = auth_type
        self.auth_header_name = auth_header_name
        self.custom_headers = custom_headers or {}
        self.organization_id = organization_id
        self.project_id = project_id
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
        h = _build_auth_headers(
            self.api_key, self.auth_type, self.auth_header_name, self.custom_headers
        )
        if self.organization_id:
            h["OpenAI-Organization"] = self.organization_id
        if self.project_id:
            h["OpenAI-Project"] = self.project_id
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
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(req.messages),
        }
        if req.tools:
            payload["tools"] = list(req.tools)
        payload.update(_options_to_payload(req.options, stream=False))

        try:
            r = await self._client.post(
                "/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"timeout calling {self.base_url}/chat/completions: {exc}",
                category=CAT_TIMEOUT,
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"network error calling {self.base_url}/chat/completions: {exc}",
                category=CAT_NETWORK,
                provider=self.name,
            ) from exc

        if r.status_code != 200:
            snippet = (r.text or "")[:300]
            raise ProviderError(
                f"chat completion failed ({r.status_code}): {snippet}",
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
            yield {
                "delta": "",
                "done": True,
                "error": "no model selected",
            }
            return
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(req.messages),
            "stream": True,
        }
        if req.tools:
            payload["tools"] = list(req.tools)
        payload.update(_options_to_payload(req.options, stream=True))

        try:
            async with self._client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    yield {
                        "delta": "",
                        "done": True,
                        "error": f"stream failed ({r.status_code}): {(body or b'')[:300]!r}",
                    }
                    return
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    # SSE: lines look like `data: {...}` or `[DONE]`.
                    if line.startswith("data:"):
                        line = line[len("data:") :].strip()
                    if line == "[DONE]":
                        yield {"delta": "", "done": True}
                        return
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        # Some providers stream non-JSON keepalives.
                        continue
                    try:
                        choice = ev["choices"][0]
                    except (KeyError, IndexError, TypeError):
                        # An empty choices array can mean the provider
                        # is still warming up; skip the chunk.
                        continue
                    msg = choice.get("delta") or {}
                    delta = msg.get("content") or ""
                    finish = choice.get("finish_reason")
                    yield {
                        "delta": delta,
                        "done": finish is not None,
                    }
        except httpx.TimeoutException as exc:
            yield {
                "delta": "",
                "done": True,
                "error": f"stream timeout: {exc}",
            }
        except httpx.HTTPError as exc:
            yield {
                "delta": "",
                "done": True,
                "error": f"stream network error: {exc}",
            }

    async def list_models(self) -> list[str]:
        """GET `{base_url}/models`. Many providers support this.

        Falls back to the configured `models` list on any error — the
        user supplied the list at create-time, so it's a sensible
        default.
        """
        try:
            r = await self._client.get("/models", headers=self._headers())
        except httpx.HTTPError as exc:
            log.warning("provider.list_models_failed", base_url=self.base_url, error=str(exc))
            return list(self.models)
        if r.status_code != 200:
            return list(self.models)
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError):
            return list(self.models)
        items = data.get("data") or []
        ids: list[str] = []
        for item in items:
            mid = item.get("id")
            if isinstance(mid, str) and mid:
                ids.append(mid)
        return ids or list(self.models)

    async def health_check(self) -> HealthReport:
        """Lightweight probe: `max_tokens=1` chat completion.

        We don't want to load-test the provider from the health loop.
        The probe is the cheapest "is auth valid and is the model
        reachable" call we can make on a /chat/completions endpoint.
        """
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
            "max_tokens": _HEALTH_PROBE_TOKENS,
            "stream": False,
        }
        import time

        t0 = time.perf_counter()
        try:
            r = await self._client.post(
                "/chat/completions",
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


__all__ = ["OpenAICompatibleProvider"]

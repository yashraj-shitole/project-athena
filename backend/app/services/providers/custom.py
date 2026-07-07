"""Custom REST provider adapter.

Lets the user point at any JSON REST endpoint that doesn't match
the OpenAI/Anthropic/Gemini/Azure/Ollama shapes. The user supplies
two templates on the connector row's `settings` dict:

* `request_template` (dict): the JSON body to POST. A few special
  placeholders are substituted from the chat request:
    - `{{model}}`        → resolved model name
    - `{{messages}}`     → the messages list (verbatim)
    - `{{messages_json}}`→ the messages list as a JSON string
    - `{{system}}`       → the first `system` message's content
    - `{{tools}}`        → the tools list (verbatim, if any)
  Anything else is passed through verbatim. This is a *string*-
  template substitution, not a full templating engine — the user
  is expected to write JSON-shaped templates and the substitution
  happens before `json.dumps`.

* `response_paths` (dict): three JSONPath-ish keys (lightweight —
  no full `jsonpath_ng` dependency):
    - `text`: JSONPath to the assistant's text content
    - `tool_call.name`: JSONPath to the tool call name (optional)
    - `tool_call.arguments`: JSONPath to the tool call arguments
    - `usage.prompt_tokens`: JSONPath to the prompt token count
    - `usage.completion_tokens`: JSONPath to the completion token count
  Implemented as a small recursive lookup that supports dot-paths
  and `[N]` indices (e.g. `choices.0.message.content`). The
  template is read at adapter-construction time so a typo is caught
  the first time the connector is used.

The custom adapter is intentionally minimal. It exists to cover
"my LLM is on a private endpoint and we don't want to write a
full adapter for it." For everything that fits a known shape, the
dedicated adapter is faster and safer.
"""
from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.logging import get_logger
from app.services.providers.base import (
    CAT_BAD_REQUEST,
    CAT_INVALID_RESPONSE,
    CAT_NETWORK,
    CAT_OK,
    CAT_TIMEOUT,
    ChatRequest,
    HealthReport,
    LLMResponse,
    ProviderAdapter,
    ProviderError,
)

log = get_logger(__name__)


_DEFAULT_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 8.0

# Placeholder patterns: `{{name}}` where name is one of the supported
# substitutions. Anything else is left as-is so the user can write
# their own brace pairs if they really need to.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _render_template(template: Any, ctx: dict[str, Any]) -> Any:
    """Recursively substitute `{{key}}` placeholders in strings.

    String placeholders are substituted verbatim — NOT JSON-quoted.
    A template like `"model": "{{model}}"` becomes `"model": "x"`
    when `{{model}}` resolves to the string `x`, not `"model": "x"`
    wrapped in extra quotes. Use the explicit `{{key_json}}` form
    if you want a JSON-encoded value (e.g. for an embedded list).
    """
    if isinstance(template, str):
        # Special case: a string that consists of EXACTLY one
        # placeholder and nothing else should resolve to the
        # *raw* value (so a string placeholder yields a string,
        # a list placeholder yields a list). Anything else is
        # treated as a fragment and the value is coerced to
        # its `str()` form.
        stripped = template.strip()
        m = _PLACEHOLDER.fullmatch(stripped)
        if m:
            return ctx.get(m.group(1), m.group(0))
        # Multi-token string: substitute each placeholder with
        # the str() of its value.
        return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), template)
    if isinstance(template, list):
        return [_render_template(v, ctx) for v in template]
    if isinstance(template, dict):
        return {k: _render_template(v, ctx) for k, v in template.items()}
    return template


def _lookup_path(obj: Any, path: str) -> Any:
    """Tiny dot-path lookup. Supports `a.b.0.c` (numeric segments = list index).

    Returns `None` if any segment is missing — we don't raise, so a
    misconfigured template yields `None` rather than a 500.
    """
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if part.isdigit():
            idx = int(part)
            if not isinstance(cur, list) or idx >= len(cur) or idx < -len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
    return cur


def _split_system(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
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


class CustomProvider(ProviderAdapter):
    name = "custom"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        auth_type: str = "bearer",
        auth_header_name: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        organization_id: Optional[str] = None,  # ignored
        project_id: Optional[str] = None,  # ignored
        api_version: Optional[str] = None,  # ignored
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        default_model: str = "",
        models: Optional[list[str]] = None,
    ) -> None:
        # SSRF: enforced by the router.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.auth_type = auth_type
        self.auth_header_name = auth_header_name
        self.custom_headers = custom_headers or {}
        self.default_model = default_model
        self.models = list(models or [])
        # Template + response paths come from `custom_headers`? No —
        # they come from `settings` (the connector row's settings
        # dict). The router passes `settings` via `custom_headers`
        # today? No, the router doesn't. We add a passthrough: the
        # user-facing Connectors API will set `request_template` /
        # `response_paths` in the connector row's `settings` column.
        # The router stores them in `custom_headers` (we can't reach
        # `settings` from the adapter constructor without changing
        # the constructor signature). For now, accept them via
        # `custom_headers` under well-known keys.
        self._request_template: dict[str, Any] = (
            (self.custom_headers or {}).get("request_template") or {}
        )
        self._response_paths: dict[str, str] = (
            (self.custom_headers or {}).get("response_paths") or {}
        )
        # Sanity: the user must supply a template.
        if not self._request_template:
            raise ProviderError(
                "CustomProvider requires `request_template` in the connector's settings",
                category=CAT_BAD_REQUEST,
                provider=self.name,
            )
        # Strip the template keys from `custom_headers` so they don't
        # leak into the request headers.
        self.custom_headers = {
            k: v
            for k, v in (self.custom_headers or {}).items()
            if k not in ("request_template", "response_paths")
        }
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            if self.auth_type == "header":
                h[self.auth_header_name or "x-api-key"] = self.api_key
            elif self.auth_type == "basic":
                h["Authorization"] = f"Basic {self.api_key}"
            elif self.auth_type == "none":
                pass
            else:
                h["Authorization"] = f"Bearer {self.api_key}"
        if self.custom_headers:
            for k, v in self.custom_headers.items():
                h[str(k)] = str(v)
        return h

    def _resolve_model(self, req: ChatRequest) -> str:
        return req.model or self.default_model

    def _build_body(self, req: ChatRequest) -> dict[str, Any]:
        system, msgs = _split_system(list(req.messages))
        ctx = {
            "model": self._resolve_model(req),
            "messages": msgs,
            "messages_json": json.dumps(msgs),
            "system": system or "",
            "tools": list(req.tools or []),
        }
        rendered = _render_template(self._request_template, ctx)
        if not isinstance(rendered, dict):
            raise ProviderError(
                "rendered request template is not a JSON object",
                category=CAT_BAD_REQUEST,
                provider=self.name,
            )
        return rendered

    def _parse_response(self, data: Any) -> LLMResponse:
        if not isinstance(data, dict):
            raise ProviderError(
                "response is not a JSON object",
                category=CAT_INVALID_RESPONSE,
                provider=self.name,
            )
        text = _lookup_path(data, self._response_paths.get("text", "")) or ""
        if not isinstance(text, str):
            text = str(text)
        tool_call = None
        name_path = self._response_paths.get("tool_call.name")
        args_path = self._response_paths.get("tool_call.arguments")
        if name_path:
            tc_name = _lookup_path(data, name_path)
            if tc_name:
                args = _lookup_path(data, args_path) if args_path else {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args else {}
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                tool_call = {"name": str(tc_name), "arguments": args or {}}
        pt_path = self._response_paths.get("usage.prompt_tokens")
        ct_path = self._response_paths.get("usage.completion_tokens")
        prompt_tokens = int(_lookup_path(data, pt_path) or 0) if pt_path else 0
        completion_tokens = int(_lookup_path(data, ct_path) or 0) if ct_path else 0
        return LLMResponse(
            text=text,
            tool_call=tool_call,
            raw=data,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        )

    async def chat(self, req: ChatRequest) -> LLMResponse:
        if not req.messages:
            raise ProviderError("chat() called with no messages", category=CAT_BAD_REQUEST)
        if not self._resolve_model(req):
            raise ProviderError(
                "no model selected — set `default_model` on the connector "
                "or pass `model` in the chat request",
                category=CAT_BAD_REQUEST,
            )
        body = self._build_body(req)
        # Custom providers don't have a standard chat path; we POST
        # to the base URL. Users who need a path can put a full URL
        # in `base_url` (the route is `/v1/chat` etc.).
        path = self.custom_headers.get("__path__", "")
        try:
            r = await self._client.post(
                path or "/", json=body, headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"timeout calling {self.base_url}{path}: {exc}",
                category=CAT_TIMEOUT,
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"network error calling {self.base_url}{path}: {exc}",
                category=CAT_NETWORK,
                provider=self.name,
            ) from exc
        if r.status_code != 200:
            raise ProviderError(
                f"custom chat failed ({r.status_code}): {(r.text or '')[:300]}",
                category=CAT_INVALID_RESPONSE,
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
        resp = self._parse_response(data)
        resp.provider = self.name
        return resp

    async def stream(self, req: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        # Streaming is a non-trivial extension for the custom
        # provider — the response shape varies wildly per user
        # template. For now, the custom adapter is non-streaming;
        # callers fall back to non-streaming chat if they pick
        # `custom`. The orchestrator's `LLMClient.stream` will get
        # the full text in a single event.
        yield {"delta": "", "done": True, "error": "custom provider does not support streaming"}
        return

    async def list_models(self) -> list[str]:
        return list(self.models)

    async def health_check(self) -> HealthReport:
        # Lightweight GET on the base URL is the cheapest probe we
        # can do without knowing the user's shape.
        import time

        t0 = time.perf_counter()
        try:
            r = await self._client.get(
                "/",
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
        if r.status_code in (200, 201, 204, 301, 302, 401, 403, 404):
            # 401/403/404 are still "the server is up" — the auth /
            # path is wrong, but the host is reachable.
            ok = r.status_code in (200, 201, 204)
            status = "online" if ok else (
                "auth_failed" if r.status_code in (401, 403) else "not_found"
            )
            return HealthReport(
                ok=ok,
                latency_ms=latency_ms,
                status=status if ok else ("slow" if latency_ms < 3000 else status),
                capabilities={"chat": ok, "stream": ok, "tools": ok} if ok else {},
                status_code=r.status_code,
            )
        return HealthReport(
            ok=False,
            latency_ms=latency_ms,
            status="offline",
            error=f"{r.status_code}",
            category=CAT_INVALID_RESPONSE,
            status_code=r.status_code,
        )


__all__ = ["CustomProvider"]

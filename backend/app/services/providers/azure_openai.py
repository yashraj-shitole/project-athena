"""Azure OpenAI Service adapter.

Azure's REST surface mirrors OpenAI's chat completions API, but with
two important differences:

1. The base URL is the Azure resource URL (e.g. `https://my.openai.azure.com`).
   The chat-completions path is built as
   `{base_url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}`.
   `deployment` is the *user-chosen* deployment name on Azure, NOT
   the model id. We treat the `model` field on the request as the
   deployment name; the connector's `default_model` is the default
   deployment.

2. Authentication uses the `api-key` HTTP header (NOT `Authorization: Bearer`).
   The user can still pick `auth_type=header` with a custom name for
   proxies that use a different header.

The body of the request is identical to OpenAI's chat-completions
body — we delegate the parsing to the OpenAI-compat helpers to keep
the two adapters in lock-step on `LLMResponse` / `tool_call` /
`usage` shape.
"""
from __future__ import annotations

import time
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
from app.services.providers.openai_compat import (
    _categorize_http,
    _options_to_payload,
    _parse_chat_response,
)

log = get_logger(__name__)


_DEFAULT_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 8.0
_DEFAULT_API_VERSION = "2024-02-01"


def _build_headers(
    api_key: Optional[str],
    auth_type: str,
    auth_header_name: Optional[str],
    custom_headers: Optional[dict[str, str]],
) -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        if auth_type == "header":
            h[auth_header_name or "api-key"] = api_key
        elif auth_type == "basic":
            h["Authorization"] = f"Basic {api_key}"
        else:
            # Azure's default — even when the user picks "bearer"
            # (which is what the openai_compat default would do), we
            # override to `api-key` because that's what Azure expects.
            h["api-key"] = api_key
    if custom_headers:
        for k, v in custom_headers.items():
            h[str(k)] = str(v)
    return h


class AzureOpenAIProvider(ProviderAdapter):
    name = "azure_openai"

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
        api_version: Optional[str] = None,
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
        self.api_version = api_version or _DEFAULT_API_VERSION
        self.default_model = default_model
        self.models = list(models or [])
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return _build_headers(
            self.api_key, self.auth_type, self.auth_header_name, self.custom_headers
        )

    def _resolve_deployment(self, req: ChatRequest) -> str:
        # `req.model` is the Azure deployment name when the user
        # explicitly picked one; otherwise fall back to the
        # connector's default. The model ids and the deployment
        # names are independent on Azure.
        return req.model or self.default_model

    def _path(self, deployment: str) -> str:
        return f"/openai/deployments/{deployment}/chat/completions"

    async def chat(self, req: ChatRequest) -> LLMResponse:
        if not req.messages:
            raise ProviderError("chat() called with no messages", category=CAT_BAD_REQUEST)
        deployment = self._resolve_deployment(req)
        if not deployment:
            raise ProviderError(
                "no deployment selected — set `default_model` on the connector "
                "or pass `model` in the chat request",
                category=CAT_BAD_REQUEST,
            )
        payload: dict[str, Any] = {
            "messages": list(req.messages),
        }
        if req.tools:
            payload["tools"] = list(req.tools)
        payload.update(_options_to_payload(req.options, stream=False))

        path = self._path(deployment)
        try:
            r = await self._client.post(
                path,
                params={"api-version": self.api_version},
                json=payload,
                headers=self._headers(),
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
            snippet = (r.text or "")[:300]
            raise ProviderError(
                f"azure chat failed ({r.status_code}): {snippet}",
                category=_categorize_http(r.status_code),
                status_code=r.status_code,
                provider=self.name,
            )

        try:
            data = r.json()
        except (ValueError, TypeError) as exc:
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
        deployment = self._resolve_deployment(req)
        if not deployment:
            yield {"delta": "", "done": True, "error": "no deployment selected"}
            return
        payload: dict[str, Any] = {
            "messages": list(req.messages),
            "stream": True,
        }
        if req.tools:
            payload["tools"] = list(req.tools)
        payload.update(_options_to_payload(req.options, stream=True))

        path = self._path(deployment)
        json_mod = __import__("json")
        try:
            emitted_done = False
            # Accumulate streamed tool-call fragments (see openai_compat
            # stream() for the format). Surface the first call on the
            # terminal event via an additive `tool_call` key.
            tool_acc: dict[int, dict[str, Any]] = {}

            def _finalize_tool_call() -> Optional[dict[str, Any]]:
                if not tool_acc:
                    return None
                first = tool_acc[0] if 0 in tool_acc else next(iter(tool_acc.values()))
                raw_args = first.get("arguments", "")
                if isinstance(raw_args, str) and raw_args:
                    try:
                        args = json_mod.loads(raw_args)
                    except json_mod.JSONDecodeError:
                        args = {"_raw": raw_args}
                else:
                    args = raw_args or {}
                return {"name": first.get("name") or "", "arguments": args}

            async with self._client.stream(
                "POST",
                path,
                params={"api-version": self.api_version},
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
                    if line.startswith("data:"):
                        line = line[len("data:") :].strip()
                    if line == "[DONE]":
                        ev_done: dict[str, Any] = {"delta": "", "done": True}
                        tc = _finalize_tool_call()
                        if tc:
                            ev_done["tool_call"] = tc
                        yield ev_done
                        emitted_done = True
                        return
                    try:
                        ev = json_mod.loads(line)
                    except Exception:
                        continue
                    try:
                        choice = ev["choices"][0]
                    except (KeyError, IndexError, TypeError):
                        continue
                    msg = choice.get("delta") or {}
                    delta = msg.get("content") or ""
                    for tc in msg.get("tool_calls") or []:
                        if not isinstance(tc, dict):
                            continue
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(idx, {"name": "", "arguments": ""})
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if isinstance(fn.get("arguments"), str):
                            slot["arguments"] += fn["arguments"]
                    finish = choice.get("finish_reason")
                    if finish is not None:
                        emitted_done = True
                    ev_out: dict[str, Any] = {
                        "delta": delta,
                        "done": finish is not None,
                    }
                    if finish is not None:
                        tc = _finalize_tool_call()
                        if tc:
                            ev_out["tool_call"] = tc
                    yield ev_out
            # Stream ended without [DONE] or a finish_reason chunk —
            # emit a terminal done so the SSE consumer finalizes.
            if not emitted_done:
                ev_done = {"delta": "", "done": True}
                tc = _finalize_tool_call()
                if tc:
                    ev_done["tool_call"] = tc
                yield ev_done
        except httpx.TimeoutException as exc:
            yield {"delta": "", "done": True, "error": f"stream timeout: {exc}"}
        except httpx.HTTPError as exc:
            yield {"delta": "", "done": True, "error": f"stream network error: {exc}"}

    async def list_models(self) -> list[str]:
        # Azure doesn't expose a model-list endpoint per deployment;
        # the user populates `models` on the connector at create time.
        return list(self.models)

    async def health_check(self) -> HealthReport:
        deployment = self.default_model or (self.models[0] if self.models else "")
        if not deployment:
            return HealthReport(
                ok=False,
                status="unknown",
                capabilities={},
                error="no deployment configured for health probe",
                category=CAT_UNSUPPORTED,
            )
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        path = self._path(deployment)
        t0 = time.perf_counter()
        try:
            r = await self._client.post(
                path,
                params={"api-version": self.api_version},
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


__all__ = ["AzureOpenAIProvider"]

"""Google Gemini (Generative Language) adapter.

Wire format used:

* Non-streaming: `POST {base_url}/v1beta/models/{model}:generateContent?key={api_key}`
* Streaming:     `POST {base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse&key={api_key}`
* Model list:   `GET  {base_url}/v1beta/models?key={api_key}`

Notable shape differences from the OpenAI-compat path:

* The system prompt lives under `system_instruction.parts[*].text`.
* The user/assistant `contents` entries are `{role, parts: [{text: ...}]}`.
* Tools are declared as `tools[].functionDeclarations[*]` and tool
  calls come back in `candidates[0].content.parts[*].functionCall`.
* There is no top-level `usage` block on `generateContent` — usage
  lives under `usageMetadata` with `promptTokenCount` /
  `candidatesTokenCount` / `totalTokenCount`.

Authentication is the `?key=...` query param, NOT a header. The
adapter always uses query-param auth regardless of `auth_type`.
The user can pass `auth_type=header` with a custom header if they
proxy Gemini through something else.
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
    CAT_UNKNOWN,
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
_TEMPERATURE_DEFAULT = 0.2


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
    """Collect every `role == "system"` message into `system_instruction`.

    Gemini takes the system prompt under `system_instruction.parts[*].text`;
    any `system` entry left in `contents` is silently dropped by
    `_contents`. The earlier version only captured the first system
    message, so a second one (e.g. the orchestrator's corrective retry
    note) was lost. We now merge all of them.
    """
    out: list[dict] = []
    system_parts: list[str] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        out.append(m)
    system = "\n\n".join(system_parts) if system_parts else None
    return system, out


def _contents(messages: list[dict]) -> list[dict]:
    """Translate OpenAI messages to Gemini's `contents` shape.

    Gemini uses `user` / `model` roles; `assistant` → `model`.
    `parts: [{text: "..."}]` wraps the content.
    """
    out: list[dict] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role in ("assistant", "model"):
            role = "model"
        elif role in ("user",):
            role = "user"
        else:
            # Skip stray `system` here (it's already split out by
            # `_split_system`); skip other unrecognised roles.
            continue
        content = m.get("content")
        if isinstance(content, list):
            # Already in `parts` form; pass through.
            parts = content
        else:
            parts = [{"text": content or ""}]
        out.append({"role": role, "parts": parts})
    return out


def _tools_gemini(tools: Optional[list[dict]]) -> Optional[list[dict]]:
    if not tools:
        return None
    decls: list[dict] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or t
        if not fn:
            continue
        decls.append(
            {
                "name": fn.get("name") or t.get("name") or "",
                "description": fn.get("description") or t.get("description") or "",
                "parameters": fn.get("parameters")
                or t.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )
    if not decls:
        return None
    return [{"functionDeclarations": decls}]


def _options_to_payload(options: Optional[dict]) -> dict[str, Any]:
    """Translate `options` to Gemini's `generationConfig` shape.

    Cross-vendor aliases are mapped so the orchestrator's shared
    options dict (OpenAI/Ollama naming) still reaches Gemini:
      `stop`         -> `stopSequences`
      `num_predict`  -> `maxOutputTokens`  (Ollama answer cap)
      `response_format` / `json_mode` -> `responseMimeType: application/json`
    """
    if not options:
        return {}
    out: dict[str, Any] = {}
    direct = {
        "temperature": "temperature",
        "top_p": "topP",
        "top_k": "topK",
        "max_tokens": "maxOutputTokens",
        "stop_sequences": "stopSequences",
        "candidate_count": "candidateCount",
    }
    gen: dict[str, Any] = {}
    for k, v in options.items():
        if k in direct:
            gen[direct[k]] = v
        elif k == "num_predict":
            try:
                gen["maxOutputTokens"] = int(v)
            except (TypeError, ValueError):
                pass
        elif k == "stop":
            if isinstance(v, (list, tuple)):
                gen["stopSequences"] = list(v)
            elif v:
                gen["stopSequences"] = [str(v)]
        elif k == "response_format" and isinstance(v, dict):
            # OpenAI json_object -> Gemini application/json.
            if v.get("type") == "json_object" or v.get("type") == "json_schema":
                gen["responseMimeType"] = "application/json"
        elif k == "json_mode" and v:
            gen["responseMimeType"] = "application/json"
    if gen:
        out["generationConfig"] = gen
    return out


def _auth(api_key: Optional[str], custom_headers: dict[str, str]) -> dict[str, Any]:
    """Return a dict with `params` and `headers` for httpx calls.

    Gemini takes the API key as a query parameter by default. If the
    user supplies `auth_type=header`, we use a custom header instead
    (so the adapter can talk to a proxy that injects the key).
    """
    headers = {"Content-Type": "application/json"}
    params: dict[str, Any] = {}
    if api_key:
        params["key"] = api_key
    if custom_headers:
        for k, v in custom_headers.items():
            headers[str(k)] = str(v)
    return {"params": params, "headers": headers}


def _parse_chat_response(data: dict[str, Any]) -> LLMResponse:
    candidates = data.get("candidates") or []
    text_parts: list[str] = []
    tool_call = None
    if candidates:
        first = candidates[0] or {}
        content = first.get("content") or {}
        parts = content.get("parts") or []
        if isinstance(parts, list):
            for p in parts:
                if not isinstance(p, dict):
                    continue
                if "text" in p and p["text"]:
                    text_parts.append(p["text"])
                fn = p.get("functionCall")
                if fn and tool_call is None:
                    raw_args = fn.get("args") or {}
                    args = raw_args
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {"_raw": args}
                    tool_call = {
                        "name": fn.get("name") or "",
                        "arguments": args or {},
                    }
    text = "".join(text_parts)
    usage = data.get("usageMetadata") or {}
    return LLMResponse(
        text=text,
        tool_call=tool_call,
        raw=data,
        usage={
            "prompt_tokens": int(usage.get("promptTokenCount") or 0),
            "completion_tokens": int(usage.get("candidatesTokenCount") or 0),
            "total_tokens": int(usage.get("totalTokenCount") or 0),
        },
    )


class GeminiProvider(ProviderAdapter):
    name = "gemini"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        auth_type: str = "bearer",  # mostly ignored; query-param auth by default
        auth_header_name: Optional[str] = None,  # used when auth_type == "header"
        custom_headers: Optional[dict[str, str]] = None,
        organization_id: Optional[str] = None,  # ignored
        project_id: Optional[str] = None,  # ignored
        api_version: Optional[str] = None,  # ignored — version is in the URL path
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
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _auth_kwargs(self) -> dict[str, Any]:
        a = _auth(self.api_key, self.custom_headers)
        if self.auth_type == "header" and self.api_key:
            # Promote the api_key from query param to a header.
            a["params"].pop("key", None)
            a["headers"][self.auth_header_name or "x-goog-api-key"] = self.api_key
        return a

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
            "contents": _contents(msgs),
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        if req.tools:
            tools = _tools_gemini(req.tools)
            if tools:
                payload["tools"] = tools
        # Default to a low temperature if the orchestrator didn't pin one.
        # Merge the translated options INTO the generationConfig so a
        # default temperature isn't clobbered when other options are set.
        gen_cfg = payload.setdefault("generationConfig", {})
        if not (req.options or {}).get("temperature"):
            gen_cfg["temperature"] = _TEMPERATURE_DEFAULT
        opts = _options_to_payload(req.options)
        if opts.get("generationConfig"):
            gen_cfg.update(opts["generationConfig"])

        auth = self._auth_kwargs()
        path = f"/v1beta/models/{model}:generateContent"
        try:
            r = await self._client.post(
                path, params=auth["params"], json=payload, headers=auth["headers"]
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
                f"gemini chat failed ({r.status_code}): {snippet}",
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
        payload: dict[str, Any] = {"contents": _contents(msgs)}
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        if req.tools:
            tools = _tools_gemini(req.tools)
            if tools:
                payload["tools"] = tools
        gen_cfg = payload.setdefault("generationConfig", {})
        if not (req.options or {}).get("temperature"):
            gen_cfg["temperature"] = _TEMPERATURE_DEFAULT
        opts = _options_to_payload(req.options)
        if opts.get("generationConfig"):
            gen_cfg.update(opts["generationConfig"])

        auth = self._auth_kwargs()
        path = f"/v1beta/models/{model}:streamGenerateContent"
        # `alt=sse` flips the streaming format from newline-delimited
        # JSON to the `data: ...` SSE we know how to parse.
        params = dict(auth["params"])
        params["alt"] = "sse"
        try:
            emitted_done = False
            # Gemini streams a functionCall as a complete part (args are
            # not fragmented). Capture the first one and surface it on
            # the terminal event via an additive `tool_call` key.
            tool_call: Optional[dict[str, Any]] = None
            async with self._client.stream(
                "POST", path, params=params, json=payload, headers=auth["headers"]
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
                    if not line or not line.startswith("data:"):
                        continue
                    line = line[len("data:") :].strip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    candidates = ev.get("candidates") or []
                    if not candidates:
                        continue
                    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
                    if not isinstance(parts, list):
                        continue
                    delta_text = ""
                    for p in parts:
                        if not isinstance(p, dict):
                            continue
                        if "text" in p and p["text"]:
                            delta_text += p["text"]
                        fn = p.get("functionCall")
                        if fn and tool_call is None:
                            raw_args = fn.get("args") or {}
                            if isinstance(raw_args, str):
                                try:
                                    raw_args = json.loads(raw_args) if raw_args else {}
                                except json.JSONDecodeError:
                                    raw_args = {"_raw": raw_args}
                            tool_call = {
                                "name": fn.get("name") or "",
                                "arguments": raw_args or {},
                            }
                    finish = (candidates[0] or {}).get("finishReason")
                    # `STOP` is Gemini's normal completion reason. Any
                    # non-None finishReason (STOP, MAX_TOKENS, SAFETY,
                    # RECITATION, ...) marks the final chunk — yield
                    # done=True and stop. The previous code treated STOP
                    # as "not done", so the generator returned without a
                    # terminal done event and the SSE consumer hung.
                    if finish is not None:
                        emitted_done = True
                        ev_done: dict[str, Any] = {"delta": delta_text, "done": True}
                        if tool_call is not None:
                            ev_done["tool_call"] = tool_call
                        yield ev_done
                        return
                    yield {"delta": delta_text, "done": False}
            # Stream closed without a finishReason — emit terminal done.
            if not emitted_done:
                ev_done = {"delta": "", "done": True}
                if tool_call is not None:
                    ev_done["tool_call"] = tool_call
                yield ev_done
        except httpx.TimeoutException as exc:
            yield {"delta": "", "done": True, "error": f"stream timeout: {exc}"}
        except httpx.HTTPError as exc:
            yield {"delta": "", "done": True, "error": f"stream network error: {exc}"}

    async def list_models(self) -> list[str]:
        auth = self._auth_kwargs()
        try:
            r = await self._client.get(
                "/v1beta/models", params=auth["params"], headers=auth["headers"]
            )
        except httpx.HTTPError as exc:
            log.warning("provider.list_models_failed", base_url=self.base_url, error=str(exc))
            return list(self.models)
        if r.status_code != 200:
            return list(self.models)
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError):
            return list(self.models)
        items = data.get("models") or []
        ids: list[str] = []
        # Gemini returns model names like "models/gemini-1.5-pro-latest";
        # we strip the prefix so the user sees just the model id.
        for item in items:
            name = item.get("name")
            if isinstance(name, str) and name:
                ids.append(name.split("/", 1)[-1])
        return ids or list(self.models)

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
            "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        }
        auth = self._auth_kwargs()
        path = f"/v1beta/models/{model}:generateContent"
        t0 = time.perf_counter()
        try:
            r = await self._client.post(
                path,
                params=auth["params"],
                json=payload,
                headers=auth["headers"],
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


__all__ = ["GeminiProvider"]

"""High-level LLM client used by the orchestrator.

After the External Model Connector (EMC) module shipped, this class
is a thin facade over a resolved `ProviderAdapter`. The orchestrator
keeps calling `await llm.complete(...)` and `async for ev in
llm.stream(...)` — the contract is unchanged.

The PAL seam lives in `app.services.providers`. We resolve the
adapter here (per turn) instead of at module load so the same code
can route to user-registered external providers AND keep the
built-in Ollama fallback working. If no connector is configured
the router returns an `OpenAICompatibleProvider` pointed at
`settings.OLLAMA_BASE_URL` — which the existing Ollama client
already happened to do, so Phase 1 callers see byte-for-byte
identical behaviour.

The class is no longer a process-wide singleton: the orchestrator
constructs a fresh `LLMClient` per turn so the user_id + session +
connector_id are bound to the right request. The router keeps an
adapter cache (`self._router._cache`) so we don't allocate a new
`httpx.AsyncClient` on every turn.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.providers import base as pal
from app.services.providers.router import ModelRouter

log = get_logger(__name__)
_settings = get_settings()


def _first_tool_call(message: dict) -> dict | None:
    """Ollama-style `message.tool_calls` (list) → single normalized call.

    The PAL adapters already normalize provider responses into
    `LLMResponse.tool_call`. This helper exists for the rare path
    where the orchestrator needs to peek at a raw message dict
    (e.g. when extending the agent for ad-hoc tests).
    """
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    first = calls[0]
    fn = first.get("function") or {}
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    return {
        "name": fn.get("name") or first.get("name") or "",
        "arguments": args or {},
    }


def _build_options() -> dict[str, Any]:
    return {
        "temperature": 0.2,
        "num_ctx": _settings.TOKEN_BUDGET_TOTAL + 64,
        "num_predict": _settings.TOKEN_BUDGET_ANSWER,
    }


class LLMResponse:
    """Normalized LLM response (text + optional single tool call).

    Kept as a class (not a dataclass) to match the historical shape
    the orchestrator relies on. The PAL's `LLMResponse` is a
    dataclass with extra fields (usage, provider) — we copy those
    over so callers can read them through the legacy attribute names.
    """

    __slots__ = (
        "text",
        "tool_call",
        "raw",
        "usage",
        "provider",
        "connector_id",
        "model",
    )

    def __init__(
        self,
        text: str = "",
        tool_call: dict | None = None,
        raw: Any = None,
        *,
        usage: Optional[dict[str, int]] = None,
        provider: str = "",
        connector_id: Optional[uuid.UUID] = None,
        model: str = "",
    ) -> None:
        self.text = text
        self.tool_call = tool_call
        self.raw = raw
        self.usage = usage or {}
        self.provider = provider
        self.connector_id = connector_id
        self.model = model


class LLMClient:
    """Per-turn LLM facade. Construct one per chat turn.

    The constructor resolves the connector → adapter once. The
    agent then calls `complete()` and/or `stream()`. The resolved
    `(adapter, model, connector_id)` is cached on the instance
    so the agent can read `self.last_resolved_*` after the call
    (used for the usage row + the assistant Message's
    `connector_id` / `model` columns).
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        connector_id: Optional[uuid.UUID] = None,
        model: Optional[str] = None,
        router: Optional[ModelRouter] = None,
    ) -> None:
        self._session = session
        self._user_id = user_id
        self._request_connector_id = connector_id
        self._request_model = model
        # One router per process (it holds an adapter cache). The
        # orchestrator instantiates the router at app startup; for
        # tests we let the agent pass one in.
        self._router = router or ModelRouter()
        # Resolved at first call so a constructed-but-unused client
        # doesn't run a SQL query.
        self._adapter: Optional[pal.ProviderAdapter] = None
        self._model: str = ""
        self._resolved_connector_id: Optional[uuid.UUID] = None
        # Per-turn metrics; the agent reads these after a turn to
        # write the `connector_usage` row.
        self.last_latency_ms: int = 0
        self.last_provider: str = ""
        self.last_usage: dict[str, int] = {}

    async def aclose(self) -> None:
        # Adapters are cached on the router, not the client — closing
        # the client doesn't tear down the upstream connection.
        return None

    async def _resolve(self) -> pal.ProviderAdapter:
        if self._adapter is None:
            self._adapter, self._model, self._resolved_connector_id = (
                await self._router.resolve(
                    self._session,
                    self._user_id,
                    connector_id=self._request_connector_id,
                    model_hint=self._request_model,
                )
            )
            log.info(
                "llm.resolve",
                connector_id=str(self._resolved_connector_id) if self._resolved_connector_id else None,
                model=self._model,
                provider=self._adapter.name,
                user_id=str(self._user_id),
            )
        return self._adapter

    @property
    def resolved_model(self) -> str:
        return self._model

    @property
    def resolved_connector_id(self) -> Optional[uuid.UUID]:
        return self._resolved_connector_id

    async def complete(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        options: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Non-streaming completion.

        Provider errors are NOT swallowed: returning an empty
        response here would make `run_turn` silently persist an
        empty assistant message and answer 200 OK, hiding the
        outage from the user. Let it raise so `stream_turn` can
        emit RUN_ERROR and `run_turn` surfaces a real failure to
        the caller.
        """
        adapter = await self._resolve()
        opts = options or _build_options()
        model = self._request_model or self._model

        try:
            log.info("llm.debug.prompt", messages=messages, tools=bool(tools))
        except Exception:
            pass

        t0 = time.perf_counter()
        pal_resp = await adapter.chat(
            pal.ChatRequest(
                messages=messages,
                tools=tools or None,
                options=opts,
                stream=False,
                model=model,
            )
        )
        self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
        self.last_provider = adapter.name
        self.last_usage = pal_resp.usage or {}

        try:
            log.info(
                "llm.debug.response",
                text=pal_resp.text,
                has_tool_call=bool(pal_resp.tool_call),
            )
        except Exception:
            pass

        return LLMResponse(
            text=pal_resp.text or "",
            tool_call=pal_resp.tool_call,
            raw=pal_resp.raw,
            usage=pal_resp.usage or {},
            provider=adapter.name,
            connector_id=self._resolved_connector_id,
            model=model,
        )

    async def stream(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict]:
        """Yield text deltas as they arrive. Each yield is a dict
        {delta: str, done: bool, error: str | None}.

        Mirrors the historical Ollama shape so `agent.py`'s SSE
        consumer keeps working. Streaming errors are emitted as a
        final `{"error": ..., "done": True}` event rather than
        raised — the agent turns that into a RUN_ERROR SSE event.
        """
        adapter = await self._resolve()
        opts = options or _build_options()
        model = self._request_model or self._model
        t0 = time.perf_counter()

        try:
            async for ev in adapter.stream(
                pal.ChatRequest(
                    messages=messages,
                    tools=tools or None,
                    options=opts,
                    stream=True,
                    model=model,
                )
            ):
                yield ev
        except pal.ProviderError as exc:
            yield {
                "delta": f"[llm error: {exc}]",
                "done": True,
                "error": str(exc),
            }
        finally:
            self.last_latency_ms = int((time.perf_counter() - t0) * 1000)
            self.last_provider = adapter.name


__all__ = ["LLMClient", "LLMResponse", "_first_tool_call", "get_llm", "close_llm"]


# ---------------------------------------------------------------------
# Backwards-compat shims for the old singleton API.
#
# Pre-EMC code did `from app.services.orchestrator.llm_client import
# get_llm` and then `llm = get_llm()`. The new `LLMClient` is
# per-turn and needs (session, user_id, connector_id, model), so
# the singleton pattern no longer fits. The agent now constructs
# `LLMClient` directly. `get_llm` is preserved as a no-op so any
# future caller that still imports it gets a clear error rather
# than a confusing AttributeError.
# ---------------------------------------------------------------------
def get_llm() -> "LLMClient":  # pragma: no cover
    raise RuntimeError(
        "get_llm() is no longer supported after the EMC module "
        "shipped. Construct LLMClient(session, user_id=…, "
        "connector_id=…, model=…) per turn instead."
    )


async def close_llm() -> None:  # pragma: no cover
    return None

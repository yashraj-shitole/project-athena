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


# ---------------------------------------------------------------------
# M-31 — PII redaction in debug logs.
#
# The prompt and response can contain anything a user pastes in:
# email addresses, API keys, phone numbers, names, the contents of
# an uploaded document. Logging the raw `messages` and `text` is a
# data-handling hazard; logs are far harder to redact after the
# fact than they are to keep clean in the first place.
#
# The fingerprint below lets an operator verify the conversation is
# being sent as expected (correct count, correct total size, correct
# role distribution) without ever persisting the contents. If a
# dispute ever needs the actual content, the audit log in
# `audit.record` keeps the conversation id, the connector id, and
# the model — the orchestrator's caller (the user) is the source of
# truth for the text.
# ---------------------------------------------------------------------
_PII_REDACTED = "[redacted]"


def _fingerprint_role(role: Any) -> str:
    """Normalize a message role to one of {system, user, assistant, tool, other}."""
    if not isinstance(role, str):
        return "other"
    r = role.lower()
    if r in ("system", "user", "assistant", "tool"):
        return r
    return "other"


def _fingerprint_text(text: Any) -> tuple[int, int]:
    """Return (char_count, content_hash_prefix) for a single content fragment.

    Hashes are not stored — only the first 8 hex chars of a SHA-256
    digest, which is enough to tell *this run* apart from *another
    run with the same shape* without leaking the content itself.
    The fingerprint's job is to be "good enough for an operator to
    tell the conversation changed", not to be cryptographically
    meaningful.
    """
    import hashlib

    if not isinstance(text, str):
        return 0, ""
    s = text
    if not s:
        return 0, ""
    digest = hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()
    return len(s), digest[:8]


def _fingerprint_content(content: Any) -> dict[str, Any]:
    """Fingerprint a single message's ``content`` field.

    The content can be a string, a list of content-part dicts
    (the OpenAI multimodal shape), or None. Each text part is
    length- and hash-fingerprinted; image / file parts are counted
    but their payload is not inspected.
    """
    if content is None:
        return {"parts": 0, "chars": 0, "hash": ""}
    if isinstance(content, str):
        chars, h = _fingerprint_text(content)
        return {"parts": 1, "chars": chars, "hash": h}
    if isinstance(content, list):
        total_chars = 0
        first_hash = ""
        text_parts = 0
        nontext_parts = 0
        for part in content:
            if isinstance(part, dict):
                ptype = part.get("type")
                if ptype in ("text", None):
                    text_parts += 1
                    chars, h = _fingerprint_text(part.get("text", ""))
                    total_chars += chars
                    if not first_hash and h:
                        first_hash = h
                else:
                    # image / file / audio — count it but don't peek
                    nontext_parts += 1
        return {
            "parts": len(content),
            "text_parts": text_parts,
            "nontext_parts": nontext_parts,
            "chars": total_chars,
            "hash": first_hash,
        }
    # Unknown shape — return the size of the repr but not the content
    return {"parts": 1, "chars": len(repr(content)), "hash": ""}


def _fingerprint_messages(messages: list[Any]) -> dict[str, Any]:
    """Return a structural fingerprint of a prompt's message list.

    Output shape::

        {
            "count": <int>,                # number of messages
            "total_chars": <int>,          # sum of text lengths
            "roles": {"system": <n>, "user": <n>, ...},
            "has_tools": <bool>,           # any tool/function message?
            "first_user_chars": <int>,     # size of the first user msg
        }

    The actual content of any message is never included.
    """
    if not isinstance(messages, list):
        return {
            "count": 0,
            "total_chars": 0,
            "roles": {},
            "has_tools": False,
            "first_user_chars": 0,
        }
    roles: dict[str, int] = {}
    total = 0
    first_user = 0
    has_tool = False
    for m in messages:
        if not isinstance(m, dict):
            roles["other"] = roles.get("other", 0) + 1
            continue
        role = _fingerprint_role(m.get("role"))
        roles[role] = roles.get(role, 0) + 1
        if role == "tool":
            has_tool = True
        fp = _fingerprint_content(m.get("content"))
        total += fp["chars"]
        if role == "user" and first_user == 0 and fp["chars"]:
            first_user = fp["chars"]
    return {
        "count": len(messages),
        "total_chars": total,
        "roles": roles,
        "has_tools": has_tool,
        "first_user_chars": first_user,
    }


def _fingerprint_response(pal_resp: Any, model: str = "") -> dict[str, Any]:
    """Return a structural fingerprint of an LLMResponse.

    Captures the *shape* of the response (length, presence of a
    tool call, model, usage) without ever persisting the actual
    text the model produced.

    The ``model`` argument is the orchestrator's resolved model
    name, since the PAL's ``LLMResponse`` does not carry a model
    field — the orchestrator tracks the model separately.
    """
    text = getattr(pal_resp, "text", "") or ""
    if not isinstance(text, str):
        text = ""
    tool_call = getattr(pal_resp, "tool_call", None)
    usage = getattr(pal_resp, "usage", None) or {}
    resp_model = getattr(pal_resp, "model", None) or model or ""
    return {
        "chars": len(text),
        "has_tool_call": tool_call is not None,
        "tool_name": (tool_call or {}).get("name", "") if isinstance(tool_call, dict) else "",
        "model": resp_model if isinstance(resp_model, str) else "",
        "usage": {k: v for k, v in usage.items() if isinstance(v, (int, float))} if isinstance(usage, dict) else {},
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
            # M-31 — never log the raw prompt. A user message may
            # contain email addresses, names, API keys, or other
            # PII. Log the structural fingerprint (count, total
            # chars, role distribution) so an operator can verify
            # the conversation is being sent as expected, without
            # persisting the contents.
            log.info("llm.debug.prompt", **_fingerprint_messages(messages))
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
            # M-31 — same redaction on the response side. The
            # assistant message may also echo PII (a user asking
            # "what's my email" and the model repeating it back,
            # for example).
            log.info(
                "llm.debug.response",
                **_fingerprint_response(pal_resp, model=self._model),
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
            # M-31 — fingerprint the prompt on the streaming path
            # too. The streaming route does not have a single
            # response object at this point, so we log the prompt
            # shape and skip the response fingerprint.
            log.info("llm.debug.prompt", **_fingerprint_messages(messages))
        except Exception:
            pass

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


__all__ = [
    "LLMClient",
    "LLMResponse",
    "_first_tool_call",
    "_fingerprint_messages",
    "_fingerprint_response",
    "get_llm",
    "close_llm",
]


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

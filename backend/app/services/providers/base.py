"""Provider Abstraction Layer (PAL).

`ProviderAdapter` is the only contract every external model provider
implements. The orchestrator's `LLMClient` is the only consumer, and it
delegates everything to the adapter resolved by `ModelRouter`.

Why an ABC instead of duck-typing: the chat engine and the health
probe both classify failures with a stable category string
(`ProviderError.category`). A typo in the attribute would silently
break dashboards; making the abstract method `category` a class-level
constant catches it at import time.

The wire format returned by `chat()` and `stream()` mirrors the
existing `LLMClient` contract — `LLMResponse` (text + single tool call)
and `{"delta", "done", "error"}` SSE-shaped dicts. The orchestrator
keeps working unchanged.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional


# --- Error categories ----------------------------------------------------
# Stable vocabulary; the dashboard filter and the health probe both
# branch on these strings. Rename only with a coordinated frontend
# change.

CAT_OK = "ok"
CAT_AUTH = "auth_failed"
CAT_RATE_LIMIT = "rate_limited"
CAT_NOT_FOUND = "not_found"
CAT_TIMEOUT = "timeout"
CAT_NETWORK = "network"
CAT_BAD_REQUEST = "bad_request"
CAT_SERVER = "server_error"
CAT_INVALID_RESPONSE = "invalid_response"
CAT_UNSUPPORTED = "unsupported"
CAT_UNKNOWN = "unknown"


class ProviderError(RuntimeError):
    """A failure from the provider, categorised for the dashboard.

    `category` is one of the CAT_* constants. The HTTP status (if
    known) is preserved on `status_code` so the chat engine can map it
    to a 4xx vs 5xx response without re-parsing the error.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = CAT_UNKNOWN,
        status_code: Optional[int] = None,
        provider: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.provider = provider


# --- Response shape -------------------------------------------------------

@dataclass
class LLMResponse:
    """Provider-agnostic non-streaming response.

    Mirrors `app.services.orchestrator.llm_client.LLMResponse` so the
    existing agent code keeps working without changes.
    """

    text: str = ""
    tool_call: Optional[dict[str, Any]] = None
    raw: Any = None
    usage: dict[str, int] = field(default_factory=dict)
    # Which adapter produced this — the chat engine logs it.
    provider: str = ""


# --- Capability report ----------------------------------------------------

@dataclass
class HealthReport:
    """Result of a `health_check()` call.

    `ok` is the headline boolean the dashboard shows. `capabilities`
    is a dict like `{"chat": True, "stream": True, "tools": True,
    "embeddings": False, "vision": False, "json_mode": True}`. The
    `latency_ms` is filled when we successfully reach the provider.
    `status_code` is the HTTP status the probe saw (None for network
    failures); used by the health loop to distinguish 401/403/429
    from 5xx.
    """

    ok: bool
    latency_ms: int = 0
    status: str = "unknown"  # online|offline|auth_failed|rate_limited|slow|unknown
    capabilities: dict[str, bool] = field(default_factory=dict)
    models: Optional[list[str]] = None
    error: Optional[str] = None
    category: str = CAT_UNKNOWN
    status_code: Optional[int] = None


# --- Chat request shape ---------------------------------------------------

@dataclass
class ChatRequest:
    """Provider-agnostic chat input.

    `messages` is a list of OpenAI-style role/content dicts. The
    adapter is responsible for translating to the wire format its
    provider expects (Anthropic splits system out, Gemini uses
    system_instruction, etc.).
    """

    messages: list[dict[str, Any]]
    tools: Optional[list[dict[str, Any]]] = None
    options: Optional[dict[str, Any]] = None
    stream: bool = False
    model: str = ""


# --- Abstract adapter ----------------------------------------------------

class ProviderAdapter(abc.ABC):
    """Every external model provider implements this interface."""

    #: Short identifier — e.g. "openai_compat", "anthropic", "ollama".
    name: str = ""

    @abc.abstractmethod
    async def chat(self, req: ChatRequest) -> LLMResponse:
        """Non-streaming chat completion."""

    @abc.abstractmethod
    async def stream(self, req: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        """Streaming chat completion.

        Yields `{"delta": str, "done": bool, "error": str | None}` so
        the existing `agent.py` SSE consumer keeps working unchanged.
        """

    async def list_models(self) -> list[str]:
        """Best-effort model discovery. Default: return the configured
        model list. Adapters that have a real `/models` endpoint
        (OpenAI-compat, Gemini, Ollama) override this."""
        return []

    async def health_check(self) -> HealthReport:
        """Probe the provider with a tiny request.

        Default: send a 1-token completion. Adapters that have a
        lighter probe (GET /models) override this.
        """
        return HealthReport(ok=False, status="unknown", category=CAT_UNSUPPORTED,
                            error="health_check not implemented for this provider")

    async def aclose(self) -> None:
        """Release any pooled resources. Default: no-op."""


# --- Wire-format debugging -----------------------------------------------
#
# Every adapter's `chat()` / `stream()` should log the payload it is
# about to send to the upstream so an operator can correlate a
# provider-side 4xx (e.g. "401 Unauthorized") with the exact request
# shape. The auth header is the most useful field to see during a
# 4xx but is also the most sensitive — `_redact_auth_header` masks
# everything past the scheme, so the log line is safe to ship to a
# centralized aggregator.

_AUTH_HEADER_NAMES = frozenset({"authorization", "x-api-key", "api-key"})


def _redact_auth_header(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of `headers` with sensitive auth values masked.

    The scheme word ("Bearer", "Basic") is preserved so an operator
    can confirm the wire format; the secret is replaced with
    "***<last 4 chars>". Headers that are not in the auth set are
    passed through unchanged.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in _AUTH_HEADER_NAMES and v:
            parts = v.split(" ", 1)
            if len(parts) == 2:
                scheme, secret = parts
                tail = secret[-4:] if len(secret) >= 4 else "***"
                out[k] = f"{scheme} ****{tail}"
            else:
                # no scheme prefix (e.g. "x-api-key: sk-...")
                tail = v[-4:] if len(v) >= 4 else "***"
                out[k] = f"***{tail}"
        else:
            out[k] = v
    return out


def _fingerprint_messages(messages: Any) -> dict[str, Any]:
    """Structural fingerprint of a message list — no content."""
    if not isinstance(messages, list):
        return {"count": 0, "roles": {}, "total_chars": 0, "first_user_chars": 0}
    roles: dict[str, int] = {}
    total = 0
    first_user = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "unknown"))
        roles[role] = roles.get(role, 0) + 1
        content = m.get("content")
        if isinstance(content, str):
            n = len(content)
        elif content is None:
            n = 0
        else:
            # List-of-parts (vision / tool result). Use the repr size
            # as a cheap proxy.
            n = len(repr(content))
        total += n
        if role == "user" and first_user == 0:
            first_user = n
    return {
        "count": len(messages),
        "roles": roles,
        "total_chars": total,
        "first_user_chars": first_user,
    }


def _summarize_request(
    *,
    provider: str,
    base_url: str,
    endpoint: str,
    model: str,
    headers: dict[str, str],
    messages: Any,
    tools: Any,
    options: Any,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the structured-payload dict for the ``llm.debug.request``
    log line. The auth header is masked via :func:`_redact_auth_header`
    so the secret is never written to the log.
    """
    summary: dict[str, Any] = {
        "provider": provider,
        "base_url": base_url,
        "endpoint": endpoint,
        "model": model,
        "headers": _redact_auth_header(headers),
        **_fingerprint_messages(messages),
        "has_tools": bool(tools),
        "tools_count": len(tools) if isinstance(tools, list) else 0,
        "has_options": bool(options),
        "option_keys": sorted(options.keys()) if isinstance(options, dict) else [],
    }
    if extra:
        summary.update(extra)
    return summary


__all__ = [
    "CAT_OK",
    "CAT_AUTH",
    "CAT_RATE_LIMIT",
    "CAT_NOT_FOUND",
    "CAT_TIMEOUT",
    "CAT_NETWORK",
    "CAT_BAD_REQUEST",
    "CAT_SERVER",
    "CAT_INVALID_RESPONSE",
    "CAT_UNSUPPORTED",
    "CAT_UNKNOWN",
    "ChatRequest",
    "HealthReport",
    "LLMResponse",
    "ProviderAdapter",
    "ProviderError",
]

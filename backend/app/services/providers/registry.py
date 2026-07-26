"""Provider registry — the lookup table the router uses to construct
the right adapter for a `ModelConnector` row.

Adding a new provider means: drop a new adapter file, register the
class here. Nothing else needs to change.
"""
from __future__ import annotations

import logging
from typing import Callable, Type

from app.services.providers.base import ProviderAdapter

# Module logger MUST be created before the try/except register blocks
# below — each `except` handler logs a register_failed event, and an
# earlier version defined `log` *after* those blocks, so any adapter
# import failure raised NameError inside the handler and took the whole
# registry (and therefore the app) down.
log = logging.getLogger(__name__)

# Populated below by the import-then-register dance; the OpenAI-compat
# adapter is always present. The other adapters are added in later
# phases (D), and we lazy-import them so a provider-specific import
# failure (e.g. missing optional dep) doesn't crash the rest of the
# app.
_REGISTRY: dict[str, Type[ProviderAdapter]] = {}


def register(name: str, cls: Type[ProviderAdapter]) -> None:
    """Add an adapter to the registry. The class is stored, not an
    instance — adapters need connector-specific config to construct."""
    if not name:
        raise ValueError("provider name must be non-empty")
    _REGISTRY[name] = cls


def get(name: str) -> Type[ProviderAdapter]:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown provider {name!r}. Registered: {sorted(_REGISTRY)}"
        ) from exc


def all_providers() -> list[str]:
    """Names of every registered provider. Used by the UI's
    provider-picker dropdown."""
    return sorted(_REGISTRY)


# --- Built-in providers ---------------------------------------------------

from app.services.providers.openai_compat import OpenAICompatibleProvider  # noqa: E402

register("openai_compat", OpenAICompatibleProvider)

# Phase D: register the rest of the Phase-1 providers. Order doesn't
# matter — the registry is a dict. All five lazy-import here so a
# provider-specific import failure (e.g. an optional dep) doesn't
# crash the rest of the app.
try:
    from app.services.providers.anthropic import AnthropicProvider  # noqa: E402

    register("anthropic", AnthropicProvider)
except Exception as exc:  # pragma: no cover - import-time guard
    log.warning("provider.register_failed", provider="anthropic", error=str(exc))

try:
    from app.services.providers.gemini import GeminiProvider  # noqa: E402

    register("gemini", GeminiProvider)
except Exception as exc:  # pragma: no cover
    log.warning("provider.register_failed", provider="gemini", error=str(exc))

try:
    from app.services.providers.azure_openai import AzureOpenAIProvider  # noqa: E402

    register("azure_openai", AzureOpenAIProvider)
except Exception as exc:  # pragma: no cover
    log.warning("provider.register_failed", provider="azure_openai", error=str(exc))

try:
    from app.services.providers.ollama import OllamaProvider  # noqa: E402

    register("ollama", OllamaProvider)
except Exception as exc:  # pragma: no cover
    log.warning("provider.register_failed", provider="ollama", error=str(exc))

try:
    from app.services.providers.custom import CustomProvider  # noqa: E402

    register("custom", CustomProvider)
except Exception as exc:  # pragma: no cover
    log.warning("provider.register_failed", provider="custom", error=str(exc))


__all__ = ["all_providers", "get", "register"]

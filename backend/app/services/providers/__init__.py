"""External Model Connector — provider adapters and supporting services.

This package is the public seam for the rest of the app. It exposes:

  - `ProviderAdapter` (abstract): the interface every provider implements.
  - `ProviderError`: a uniform error type with a stable `category` string
    the chat engine / health probe categorise on.
  - `ModelRouter`: resolves a (connector_id, model_hint) request to a
    (provider, model) pair — the seam the orchestrator's LLMClient uses.
  - `crypto`: Fernet-based encrypt/decrypt for connector API keys.
  - `audit`: write audit log rows from a FastAPI request.
  - `usage`: write per-request usage rows + aggregate queries.
  - `health`: the background health-probe loop and circuit breaker.

Adding a new provider means: drop a new file here, register it in
`registry.PROVIDERS`. Nothing else has to change.
"""

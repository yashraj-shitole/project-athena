# External Model Connectors (Phase D-G)

> **Status:** Implemented. 5 provider adapters (OpenAI-compat, Anthropic, Gemini, Azure OpenAI, Ollama native) + 1 custom REST adapter, REST API for CRUD + test/usage/audit, background health probe with auto-disable, full management UI in `/connectors`, model picker in the chat topbar. 150+ backend tests passing.

## What this is

The "External Model Connector" (EMC) module lets users register their own AI providers — OpenAI, Anthropic, Gemini, Azure OpenAI, Ollama, Mistral, Groq, DeepSeek, OpenRouter, LM Studio, LocalAI, or a custom REST endpoint — and have those external models act **exactly like the built-in Ollama**. After registration:

- The chat UI lets the user pick a model from the topbar.
- The orchestrator transparently routes the call through the right adapter.
- Document Q&A, RAG retrieval, agent tool calling, conversation memory, and streaming all keep working unchanged.
- A management UI (`/connectors`) handles CRUD, connection testing, model discovery, usage dashboards, and audit logs.

If no external connector is registered, behavior is byte-for-byte identical to Phase 1: every chat turn goes to the env-var-configured Ollama.

## How it works

### The Provider Abstraction Layer (PAL)

Every chat turn in Phase 1 went through `app/services/orchestrator/llm_client.py::LLMClient`, which was hard-wired to a single `OllamaClient`. The PAL replaces that with a small abstract class — `ProviderAdapter` — and a registry of concrete adapters.

```python
class ProviderAdapter(abc.ABC):
    name: str = ""
    @abstractmethod
    async def chat(self, req: ChatRequest) -> LLMResponse: ...
    @abstractmethod
    async def stream(self, req: ChatRequest) -> AsyncIterator[dict]: ...
    async def list_models(self) -> list[str]: ...        # default: []
    async def health_check(self) -> HealthReport: ...    # default: UNSUPPORTED
    async def aclose(self) -> None: ...                 # default: no-op
```

Stream dicts match the existing Ollama shape: `{"delta": str, "done": bool, "error": str | None}`. The orchestrator's SSE consumer keeps working unchanged.

### The 4-tier resolver

`ModelRouter.resolve(session, user_id, *, connector_id=None, model_hint=None)` walks a priority order:

1. **Explicit** `connector_id` from the request (if enabled, not soft-deleted, visible).
2. **User default** — `ModelConnector WHERE is_default AND user_id`.
3. **System default** — `ModelConnector WHERE is_admin AND is_default`.
4. **Fallback** — built-in Ollama constructed from `app.core.config`.

The orchestrator's `agent.py` now passes `connector_id` and `model` from the request through to `LLMClient(...)`, which calls `ModelRouter.resolve(...)` lazily on first `complete()` / `stream()`. The resolved `(adapter, model, resolved_connector_id)` is cached on the `LLMClient` instance so the same client can serve the whole turn without re-resolving.

After the turn, one row is written to `connector_usage` with token counts + latency + status, unless the resolved connector is `None` (i.e. the built-in Ollama fallback was used).

### Stable error categories

The PAL defines a stable `CAT_*` taxonomy that the health probe, the usage writer, and the chat engine all branch on:

```
CAT_OK            "ok"            — request succeeded
CAT_AUTH          "auth_failed"   — 401 / 403, invalid key
CAT_RATE_LIMIT    "rate_limited"  — 429
CAT_NOT_FOUND     "not_found"     — 404, model not found
CAT_TIMEOUT       "timeout"       — request timed out
CAT_NETWORK       "network"       — DNS / connect / read failure
CAT_BAD_REQUEST   "bad_request"   — 400, malformed request
CAT_SERVER        "server_error"  — 5xx
CAT_INVALID_RESPONSE "invalid_response" — JSON parse failed
CAT_UNSUPPORTED   "unsupported"   — feature not supported by this adapter
CAT_UNKNOWN       "unknown"       — default
```

The chat engine maps these to user-visible messages and to the `connector_usage.status` vocabulary (`success`, `auth_failed`, `rate_limited`, `not_found`, `timeout`, `network`, `bad_request`, `server_error`, `invalid_response`).

## Supported providers

| Provider          | `provider` value   | Auth default         | Notes                                                                                 |
|-------------------|--------------------|----------------------|---------------------------------------------------------------------------------------|
| OpenAI-compat     | `openai_compat`    | `bearer`             | OpenAI, OpenRouter, Groq, DeepSeek, Mistral, Together AI, LM Studio, LocalAI          |
| Anthropic         | `anthropic`        | `header` (`x-api-key`) | Sends `anthropic-version: 2023-06-01`                                                |
| Gemini            | `gemini`           | `header` (`x-goog-api-key`) | URL: `{base}/v1beta/models/{model}:generateContent?key=…`                  |
| Azure OpenAI      | `azure_openai`     | `header` (`api-key`) | URL: `{base}/openai/deployments/{deployment}/chat/completions?api-version=…`         |
| Ollama (native)   | `ollama`           | `none`               | Wire-compatible with the existing `OllamaClient`                                      |
| Custom            | `custom`           | varies               | User-supplied request template + response paths                                       |

### Custom provider

The `custom` adapter takes a `request_template` (dict, with `{{model}}`, `{{messages}}`, `{{messages_json}}`, `{{system}}`, `{{tools}}` placeholders) and `response_paths` (dict, dot-path JSONPath like `output.text`, `tool_call.name`, `usage.prompt_tokens`). It serializes the template, POSTs to `{base_url}/{path}`, and pulls text/tool-call/usage out of the response by path.

The streaming path is unsupported — the adapter yields an error event with `done=True`.

## Data model

`infra/init.sql` (idempotent, applied by `scripts/docker-up.ps1`):

### `model_connectors`

| Column                  | Type        | Notes                                                            |
|-------------------------|-------------|------------------------------------------------------------------|
| `id`                    | UUID PK     | `gen_random_uuid()`                                              |
| `user_id`               | UUID FK     | owner                                                            |
| `name`                  | TEXT        | user-facing label                                                |
| `provider`              | TEXT        | enum (see table above)                                           |
| `base_url`              | TEXT        | http/https; loopback allowed for self-hosted Ollama              |
| `api_key_enc`           | BYTEA       | Fernet-encrypted; NULL when no key needed                        |
| `api_key_preview`       | TEXT        | last-4 preview for the UI only — never the secret itself         |
| `auth_type`             | TEXT        | `bearer` / `header` / `basic` / `none`                           |
| `auth_header_name`      | TEXT        | e.g. `x-api-key`                                                 |
| `organization_id`       | TEXT        | optional                                                         |
| `project_id`            | TEXT        | optional                                                         |
| `api_version`           | TEXT        | e.g. Azure's `2024-02-01`                                        |
| `custom_headers`        | JSONB       | `{name: value}` extra headers                                    |
| `default_model`         | TEXT        | model the connector is bound to                                  |
| `models`                | JSONB       | list of exposed model ids                                        |
| `capabilities`          | JSONB       | `{chat, stream, tools, vision, embeddings, json_mode, structured}`|
| `settings`              | JSONB       | `{temperature, top_p, max_tokens, timeout_s, retry, rate_limit}` |
| `is_enabled`            | BOOLEAN     | admin kill switch (auto-disabled after N consecutive failures)   |
| `is_default`            | BOOLEAN     | user default                                                     |
| `is_admin`              | BOOLEAN     | system-wide shared (admin-only set)                              |
| `group_name`            | TEXT        | user grouping                                                    |
| `tags`                  | JSONB       | free-form labels                                                 |
| `is_favorite`           | BOOLEAN     | UI sort                                                          |
| `last_health`           | TEXT        | `online` / `offline` / `auth_failed` / `rate_limited` / `slow`   |
| `last_health_at`        | TIMESTAMPTZ |                                                                  |
| `last_health_latency_ms`| INT         |                                                                  |
| `consecutive_failures`  | INT         | circuit-breaker counter                                          |
| `discovered_models`     | JSONB       | cached list from last `/models` probe                            |
| `discovered_at`         | TIMESTAMPTZ |                                                                  |
| `created_at`            | TIMESTAMPTZ |                                                                  |
| `updated_at`            | TIMESTAMPTZ |                                                                  |
| `deleted_at`            | TIMESTAMPTZ | soft delete                                                      |

Indexes: `(user_id)`, `(is_enabled)`, `(provider)`, plus a partial unique `(user_id, name) WHERE deleted_at IS NULL`.

### `connector_audit_log`

Append-only: `(id, connector_id, user_id, action, before_redacted, after_redacted, ip, ua, at)`. The `before`/`after` fields are dumps of the Pydantic public schema, which has **no** `api_key_enc` field — the dump is naturally secret-free.

### `connector_usage`

One row per request: `(id, connector_id, user_id, model, prompt_tokens, completion_tokens, latency_ms, status, error_class, cost_estimate, at)`. `cost_estimate` is `TEXT` (parsed as `Decimal()` on the way out) for exact decimals.

### RLS

`model_connectors` policy: `user_id = athena_current_user() OR is_admin = TRUE`. Same shape for `connector_audit_log` and `connector_usage`. `FORCE ROW LEVEL SECURITY` on all three.

## Cryptography

API keys are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).

**Key resolution (priority order):**
1. `ATHENA_CONNECTOR_KEY` env var — **required in production**.
2. HKDF-SHA256 derivation from `ATHENA_JWT_SECRET` with `info=b"connector-enc-v1"` as a dev fallback. Emits a structlog warning on every decrypt.
3. Refuses to boot in non-dev environments without an explicit key.

A new key invalidates all previously-encrypted secrets — surfaced in the API + a clear `CryptoError` message. A `_INSECURE_FALLBACK_SECRETS` set refuses the dev fallback when the JWT secret is a known placeholder.

**No leakage rule.** The `ModelConnectorPublic` Pydantic schema (see `app/schemas/connector.py`) has no `api_key` or `api_key_enc` field. The preview string is precomputed and stored in `api_key_preview` at write time, so decryption is **never** performed on a read path.

## SSRF guard

Every user-supplied base URL is run through `core.ssrf.assert_safe_url(..., allow_loopback=True)` at resolution time (in the router, not the adapter constructor). Loopback is allowed because users routinely point at `http://localhost:11434` for self-hosted Ollama.

The adapter constructors trust their caller — there's a single line of defense, and the SSRF check is not duplicated per adapter.

## REST API

All under `/api/connectors`. See [api.md](api.md) for the full table. Highlights:

| Method | Path                                  | Purpose                                |
|--------|---------------------------------------|----------------------------------------|
| `GET`  | `/api/connectors`                     | list own + admin-shared                |
| `POST` | `/api/connectors`                     | create (with plaintext `api_key`)      |
| `PATCH`| `/api/connectors/{id}`                | update; `api_key=""` = no change       |
| `DELETE`| `/api/connectors/{id}`               | soft delete                            |
| `POST` | `/api/connectors/{id}/clone`          | duplicate (no secret)                  |
| `POST` | `/api/connectors/{id}/set-default`    | mark as user default                   |
| `POST` | `/api/connectors/test`                | probe a config WITHOUT saving          |
| `GET`  | `/api/connectors/{id}/health`         | last health snapshot                   |
| `GET`  | `/api/connectors/{id}/models`         | cached discovered models               |
| `POST` | `/api/connectors/{id}/refresh-models` | re-probe provider                      |
| `GET`  | `/api/connectors/{id}/usage?days=7`   | daily aggregates                       |
| `GET`  | `/api/connectors/{id}/audit`          | paginated audit log                    |
| `GET`  | `/api/connectors/templates`           | canned `provider` + `default_base_url` |
| `GET`  | `/api/connectors/registry`            | flat list of `(provider, class)`       |

The existing `/api/chat`, `/api/chat/stream` accept the new fields:

```json
{
  "message": "string",
  "conversation_id": "uuid | null",
  "tool_subset": ["search_documents"] | null,
  "connector_id": "uuid | null",
  "model": "string | null"
}
```

`/model` (the existing health endpoint) now surfaces the **active** default connector + model + base_url if the user has one set, falling back to the env-var Ollama defaults.

## Background health probe

`app/services/providers/health.py::HealthProbe` runs as a single `asyncio` task launched in the FastAPI `lifespan`. Every `connector_health_interval_s` seconds (default 60s), it walks the enabled connectors and calls `provider.health_check()`. Results are written to `last_health*` columns.

After `connector_health_failure_threshold` consecutive failures (default 3), the connector is **auto-disabled** (`is_enabled = FALSE`) and a structlog warning is emitted. A `connector_audit_log` row is written with `action=update` and `after.is_enabled=False`.

A per-connector `asyncio.Lock` map prevents two ticks from probing the same row at once. The probe is bounded by `connector_health_max_per_cycle` (default 50) so a freshly-restarted app with 1000 connectors doesn't probe them all at once.

The probe **skips** rows where `is_enabled = FALSE` or `deleted_at IS NOT NULL` — disabled rows are not retried; soft-deleted rows are not probed.

### Settings

| Env var                                 | Default | Purpose                                       |
|-----------------------------------------|---------|-----------------------------------------------|
| `ATHENA_CONNECTOR_KEY`                  | (none)  | Fernet key; required in prod                  |
| `ATHENA_CONNECTOR_HEALTH_INTERVAL_S`    | `60`    | probe cadence                                  |
| `ATHENA_CONNECTOR_HEALTH_FAILURE_THRESHOLD` | `3` | consecutive failures → auto-disable            |
| `ATHENA_CONNECTOR_HEALTH_MAX_PER_CYCLE` | `50`    | max rows probed per cycle                      |

## Frontend

| File                                              | Purpose                                |
|---------------------------------------------------|----------------------------------------|
| `src/services/connectorService.js`                 | `/api/connectors/*` wrapper            |
| `src/store/connectorsStore.js`                     | Zustand store: list, byId, activeModel |
| `src/pages/Connectors.jsx`                         | management page (list + tabs)          |
| `src/components/connectors/ConnectorCard.jsx`     | summary card                           |
| `src/components/connectors/ConnectorDialog.jsx`   | create / edit dialog                   |
| `src/components/connectors/HealthBadge.jsx`       | status pill                            |
| `src/components/connectors/CapabilityBadges.jsx`  | capability row                         |
| `src/components/connectors/TestPanel.jsx`         | run a health check                     |
| `src/components/connectors/ModelDiscoveryPanel.jsx` | discovered models + refresh         |
| `src/components/connectors/UsageDashboard.jsx`    | SVG bar chart of daily usage           |
| `src/components/connectors/AuditLogTable.jsx`     | paginated audit log                    |
| `src/components/ModelPicker.jsx`                  | compact picker in the chat topbar      |

The picker is mounted in `pages/ChatInterface.jsx`'s topbar. The active `(connectorId, model)` is persisted in `localStorage` via Zustand's `persist` middleware, so a page reload keeps the choice. The picker's value is forwarded in the chat request body; the backend's `ModelRouter` resolves it on every turn.

## Adding a new provider

To add e.g. Cohere:

1. Drop `app/services/providers/cohere.py` implementing `ProviderAdapter`.
2. Register it in `app/services/providers/registry.py::register("cohere", CohereProvider)`.
3. Add `"cohere"` to the `ALL_PROVIDERS` list in `frontend/src/components/connectors/ConnectorDialog.jsx`.
4. Add tests in `backend/tests/test_provider_adapters.py`.

That's it — the `provider` enum is data-driven, so the UI and the API both pick it up.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| 401 on every test | wrong API key, or wrong `auth_type` | Re-create the connector with the right key and `auth_type=header` if the upstream expects a custom header |
| 403 on Azure | `api-key` header missing | Set `auth_type=header` and `auth_header_name=api-key` |
| Test returns `network` | base URL not resolvable from the container | Check `docker compose exec api curl <base_url>`; localhost from outside the container is `host.docker.internal` |
| Anthropic returns 400 on `/v1/messages` | The `default_model` field is empty | Fill in the model (e.g. `claude-3-5-sonnet-20241022`) |
| Connector auto-disabled after a few minutes | Upstream is intermittently failing | Check `/api/connectors/{id}/health`; once 3 consecutive failures are recorded, the row is auto-disabled |
| "Skipping: deleted_at IS NOT NULL" in logs | You're looking at a soft-deleted row | `is_admin` rows can be reactivated by an admin via the API |
| Test returns `slow` status | Upstream is slow but eventually responds | No action needed — the connector is still considered healthy |

## Tests

| File                                            | Tests | Coverage                                          |
|-------------------------------------------------|-------|---------------------------------------------------|
| `tests/test_connector_crypto.py`                | 11    | Fernet roundtrip, mask, fallback key, prod guard  |
| `tests/test_connector_models.py`                | 10    | audit/usage writer, vocabulary, zero-state        |
| `tests/test_openai_compat.py`                   | 29    | chat, stream, SSE, all auth variants, errors      |
| `tests/test_provider_adapters.py`               | 25    | Anthropic, Gemini, Azure, Ollama, Custom         |
| `tests/test_model_router.py`                    | 11    | 4-tier resolution, RBAC, soft-delete, model hint  |
| `tests/test_connector_chat_integration.py`      | 8     | LLMClient delegation, error propagation           |
| `tests/test_connectors_api.py`                  | 20    | CRUD, masking, RBAC, templates, registry          |
| `tests/test_connector_health.py`                | 7     | probe lifecycle, auto-disable, per-cycle cap      |
| **Total**                                       | **150+** |                                                |

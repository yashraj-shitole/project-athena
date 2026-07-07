# REST + SSE API reference

All routes are mounted under `/api` except `/health`, `/model`, and `/metrics`, which are mounted at the root so that an external reverse proxy (`nginx`) can route them without the `/api` prefix.

Auth is via `Authorization: Bearer <access_token>` header. The token is a JWT signed with `ATHENA_JWT_SECRET` and valid for `ATHENA_ACCESS_TOKEN_TTL_MIN` minutes.

## Auth (FR-01…04)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Create a user. Returns `UserPublic`. |
| `POST` | `/api/auth/login` | OAuth2 form login (`username` + `password`) for the OAuth2 password flow. |
| `POST` | `/api/auth/login-json` | JSON login (`email` + `password`) — preferred for SPA clients. |
| `POST` | `/api/auth/refresh` | Exchange a refresh token for a new access token. |
| `GET`  | `/api/auth/me` | Current user (auth required). |

`TokenPair`:
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

## Documents (FR-05…09)

| Method | Path | Description |
|---|---|---|
| `POST`   | `/api/documents`              | Multipart upload. Returns `DocumentPublic` with `202 Accepted`; the ingestion runs in the background. |
| `GET`    | `/api/documents`              | List user's documents. Query: `limit`, `offset`, `status`. |
| `GET`    | `/api/documents/{id}`         | Single document. |
| `GET`    | `/api/documents/{id}/chunks`  | Inspect indexed chunks. Query: `limit`, `offset`. |
| `DELETE` | `/api/documents/{id}`         | Delete document + chunks + stored file. `204 No Content`. |

`DocumentPublic`:
```json
{
  "id": "uuid",
  "filename": "report.pdf",
  "file_type": "pdf",
  "size_bytes": 102400,
  "page_count": 12,
  "status": "uploaded | processing | indexed | failed",
  "error_message": null,
  "created_at": "...",
  "updated_at": "..."
}
```

## Chat (FR-22, FR-26, FR-31…33)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat`                       | Non-streaming turn. Returns `ChatResponse`. |
| `POST` | `/api/chat/stream`                | Streaming turn. Returns `text/event-stream`. See [architecture/streaming.md](architecture/streaming.md). |
| `POST` | `/api/chat/conversations`         | Create a conversation. |
| `GET`  | `/api/chat/conversations`         | List user's conversations, newest first. |
| `GET`  | `/api/chat/conversations/{id}`    | Get all messages in a conversation, oldest first. |
| `DELETE` | `/api/chat/conversations/{id}`  | Delete a conversation (cascades messages + tool calls). |

`ChatRequest`:
```json
{
  "message": "string",
  "conversation_id": "uuid | null",
  "tool_subset": ["search_documents"] | null,
  "connector_id": "uuid | null",
  "model": "string | null"
}
```

`connector_id` and `model` are forwarded to `ModelRouter.resolve()`. When both are `null`, the request falls back to the user's default connector, then the system default, then the built-in Ollama. See [connectors.md](connectors.md) for the full story.

The assistant `Message` carries the same fields on the way back (`connector_id`, `model`) so the chat UI can show which model answered.

`ChatResponse`:
```json
{
  "conversation_id": "uuid",
  "message": {
    "id": "uuid",
    "seq": 12,
    "role": "assistant",
    "content": "...",
    "citations": [ { "chunk_id": "uuid", "document_name": "...", "page_number": 3, "snippet": "..." } ],
    "used_tools": [ { "name": "search_documents", "status": "ok" } ],
    "connector_id": "uuid | null",
    "model": "gpt-4o-mini | null",
    "created_at": "..."
  }
}
```

## Connectors (External Model Connectors)

> See [connectors.md](connectors.md) for the architecture, data model, and provider-adapter contract. This section is the REST reference.

| Method | Path                                          | Auth        | Purpose                                      |
|--------|-----------------------------------------------|-------------|----------------------------------------------|
| `GET`    | `/api/connectors`                           | user        | list own + admin-shared                      |
| `POST`   | `/api/connectors`                           | user        | create (with plaintext `api_key`)            |
| `GET`    | `/api/connectors/{id}`                      | owner/admin | fetch one (public schema, no secret)         |
| `PATCH`  | `/api/connectors/{id}`                      | owner/admin | update; `api_key=""` = no change             |
| `DELETE` | `/api/connectors/{id}`                      | owner/admin | soft delete                                  |
| `POST`   | `/api/connectors/{id}/clone`                | user        | duplicate (omits secret)                     |
| `POST`   | `/api/connectors/{id}/set-default`          | user        | set as user default                          |
| `POST`   | `/api/connectors/test`                      | user        | probe a payload WITHOUT saving               |
| `GET`    | `/api/connectors/{id}/health`               | user        | last health snapshot                         |
| `GET`    | `/api/connectors/{id}/models`               | user        | cached discovered models                     |
| `POST`   | `/api/connectors/{id}/refresh-models`       | user        | re-probe provider                            |
| `GET`    | `/api/connectors/{id}/usage?days=7`         | owner/admin | daily aggregates                             |
| `GET`    | `/api/connectors/{id}/audit`                | owner/admin | paginated audit log                          |
| `GET`    | `/api/connectors/templates`                 | user        | canned `provider` + `default_base_url`       |
| `GET`    | `/api/connectors/registry`                  | user        | flat list of `(provider, class)`             |

`ModelConnectorCreate`:
```json
{
  "name": "My OpenAI account",
  "provider": "openai_compat",
  "base_url": "https://api.openai.com/v1",
  "auth_type": "bearer",
  "auth_header_name": null,
  "organization_id": null,
  "project_id": null,
  "api_version": null,
  "custom_headers": {},
  "default_model": "gpt-4o-mini",
  "models": ["gpt-4o-mini", "gpt-4o"],
  "capabilities": { "chat": true, "stream": true, "tools": true },
  "settings": { "timeout_s": 30, "temperature": 0.7 },
  "is_enabled": true,
  "is_admin": false,
  "is_favorite": false,
  "group_name": null,
  "tags": [],
  "api_key": "sk-..."
}
```

`ModelConnectorPublic` is the read shape and **never** carries `api_key` or `api_key_enc`. The encrypted column is decrypted only at adapter-construction time, on a single in-process hop, and the plaintext is dropped when the request finishes.

`HealthReport` (from `/test` and `/{id}/health`):
```json
{
  "ok": true,
  "latency_ms": 142,
  "status": "online",
  "capabilities": { "chat": true, "stream": true, "tools": true },
  "models": null,
  "error": null,
  "category": "ok",
  "status_code": 200
}
```

The `category` field is one of the stable `CAT_*` constants (`ok`, `auth_failed`, `rate_limited`, `not_found`, `timeout`, `network`, `bad_request`, `server_error`, `invalid_response`, `unsupported`, `unknown`).

## Tools (FR-27, FR-28, FR-29, FR-30)
```json
{
  "conversation_id": "uuid",
  "message": {
    "id": "uuid",
    "seq": 12,
    "role": "assistant",
    "content": "...",
    "citations": [ { "chunk_id": "uuid", "document_name": "...", "page_number": 3, "snippet": "..." } ],
    "used_tools": [ { "name": "search_documents", "status": "ok" } ],
    "created_at": "..."
  }
}
```

## Tools (FR-27, FR-28, FR-29, FR-30)

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/tools`                       | List registered tools. |
| `POST` | `/api/tools`                       | Upsert a tool by name. Bumps version, invalidates Redis cache. |
| `PATCH` | `/api/tools/{id}?enabled=true`    | Enable / disable a tool. |
| `GET`  | `/api/tools/snapshot`              | Cached list of Ollama-shaped tool schemas (used by the orchestrator). |
| `POST` | `/api/tools/mcp/attach?server_url=`| Discover an MCP server's tools and register them. |
| `POST` | `/api/tools/{id}/invoke`           | Ad-hoc tool invocation (no LLM). |

`ToolUpsert`:
```json
{
  "name": "send_email",
  "description": "Send an email via SMTP.",
  "parameters": { "type": "object", "properties": { "to": { "type": "string" } }, "required": ["to"] },
  "handler_type": "http",
  "handler_cfg": { "url": "https://api.example.com/email", "method": "POST" },
  "enabled": true
}
```

`handler_type` is one of:
- `internal` — resolve `handler_cfg.impl` to a Python callable, e.g. `"app.tools.builtin.search_documents:run"`.
- `http` — POST/GET/PUT/PATCH/DELETE to `handler_cfg.url` with `arguments` as JSON body.
- `mcp` — forward to a remote MCP server (`handler_cfg.server_url` + `handler_cfg.remote_name`).

## Health & ops (FR-36, FR-37, FR-39)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health`  | `db` / `redis` / `llm` reachability + latency. |
| `GET` | `/model`   | Active LLM model + provider + budget + embedding model. |
| `GET` | `/metrics` | Cache hit / miss counters + hit rate. |
| `GET` | `/docs`    | Swagger UI (FastAPI auto-generated). |

`/health` shape:
```json
{
  "status": "ok",
  "checks": {
    "db":    { "ok": true, "ms": 2 },
    "redis": { "ok": true, "ms": 1 },
    "llm":   { "ok": true, "ms": 14, "model": "qwen2.5:1.5b-instruct" }
  }
}
```

`/model` shape — if a user-default connector is registered, the response surfaces it instead of the env-var Ollama defaults:
```json
{
  "model": "gpt-4o-mini",
  "provider": "openai_compat",
  "base_url": "https://api.openai.com/v1",
  "context_budget": 3000,
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "embedding_dim": 384,
  "connector_id": "uuid"
}
```

## Error envelope

FastAPI's default 4xx/5xx body:
```json
{ "detail": "Email already registered" }
```

Or, for Pydantic validation errors, `detail` is a list of `{ "loc": [...], "msg": "...", "type": "..." }` objects. The frontend `apiClient` flattens both shapes to a single string via `e.body.detail` / `e.body.message` / `e.message`.

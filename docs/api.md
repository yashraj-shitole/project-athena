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
  "tool_subset": ["search_documents"] | null
}
```

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

## Error envelope

FastAPI's default 4xx/5xx body:
```json
{ "detail": "Email already registered" }
```

Or, for Pydantic validation errors, `detail` is a list of `{ "loc": [...], "msg": "...", "type": "..." }` objects. The frontend `apiClient` flattens both shapes to a single string via `e.body.detail` / `e.body.message` / `e.message`.

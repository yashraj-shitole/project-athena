# Tools & MCP architecture

The tool layer lets the LLM call external functions and the user manage them.

## Files

| Path | Role |
|---|---|
| `app/tools/registry.py` | CRUD + caching + dispatch (FR-27, FR-28, FR-30, FR-35). |
| `app/tools/builtin.py` | The one built-in tool: `search_documents`. |
| `app/tools/mcp.py` | Minimal JSON-RPC client for MCP servers (FR-29). |
| `app/api/tools.py` | REST routes. |

## Tool lifecycle

```
   ┌─────────────┐  upsert_tool()   ┌──────────┐
   │ /api/tools  │ ────────────────▶│  tools   │
   │   POST      │                  │  table   │
   └─────────────┘                  └────┬─────┘
                                         │
                                         ▼
                              invalidate_snapshot()
                                         │
                                         ▼
                                  ┌─────────────┐
                                  │  Redis      │
                                  │ tooldef:v1  │
                                  └─────────────┘
```

When a tool is created, updated, or its `enabled` flag is flipped, the Redis-cached snapshot is invalidated (`athena:tooldef:snapshot:v1`). The next `snapshot()` call rebuilds and re-caches it.

## Tool rows

```python
class Tool(Base):
    id: uuid
    name: str
    version: int
    description: str
    parameters: dict (JSON Schema)
    handler_type: 'internal' | 'http' | 'mcp'
    handler_cfg: dict
    enabled: bool
    is_builtin: bool
```

`handler_cfg` is a free-form JSON dict whose shape depends on `handler_type`:

| `handler_type` | `handler_cfg` shape |
|---|---|
| `internal` | `{"impl": "module.path:callable"}` |
| `http` | `{"url": "https://...", "method": "POST", "timeout_s": 20}` |
| `mcp` | `{"server_url": "https://mcp.example.com", "remote_name": "tool_name"}` |

## Built-in tool: `search_documents`

Registered in `infra/init.sql` so a fresh install has it. It exposes:

```json
{
  "name": "search_documents",
  "parameters": {
    "type": "object",
    "properties": {
      "keywords": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 16 },
      "top_k":    { "type": "integer", "minimum": 1, "maximum": 16, "default": 4 }
    },
    "required": ["keywords"]
  }
}
```

`app.tools.builtin.search_documents:run` (`alias: run`) is the actual Python function. The orchestrator injects `user_id` and `session` before calling, so the LLM never sees those arguments in the schema.

## HTTP tools

`registry._run_http(tool, arguments)`:

```python
async with httpx.AsyncClient(timeout=timeout) as client:
    resp = await client.request(method, url, json=arguments)
return {"status_code": resp.status_code, "body": _safe_json(resp)}
```

The result is returned to the LLM as-is. The LLM is then expected to summarise the response for the user.

## MCP tools (FR-29 — Phase 2 in spirit)

`app/tools/mcp.py` implements a minimal JSON-RPC client:

```python
async def discover_tools(session, server_url) -> list[Tool]:
    # POST { "jsonrpc": "2.0", "method": "tools/list", "id": 1 }
    # → list of {name, description, inputSchema}
    # for each: upsert into the tools table

async def call_tool(server_url, *, remote_name, arguments) -> dict:
    # POST { "jsonrpc": "2.0", "method": "tools/call",
    #        "params": { "name": remote_name, "arguments": arguments }, "id": 1 }
    # → { "result": ..., "error": ... } or throws MCPError
```

This is a *minimal* client — it implements `tools/list` and `tools/call` only. The full MCP spec (stdio transport, streaming notifications, sampling, roots) is Phase 2.

The orchestrator's `_execute_tool_call` calls `registry.execute()` which dispatches to `_run_mcp` for `handler_type == "mcp"`.

## Snapshot caching (FR-35)

```python
async def snapshot(session, *, use_cache=True) -> list[dict]:
    if use_cache:
        cached = await get_cached_snapshot()
        if cached is not None:
            return cached
    tools = await list_enabled(session)
    snap = [_tool_to_schema(t) for t in tools]
    await set_cached_snapshot(snap)
    return snap
```

The cache key is `athena:tooldef:snapshot:v1` and TTL is `ATHENA_CACHE_TTL_SECONDS`. `upsert_tool` and `set_enabled` both call `invalidate_snapshot`.

`select_subset()` filters the cached snapshot by name when the orchestrator was given a `tool_subset`.

## Ollama tool schema

`_tool_to_schema()` renders a `Tool` row as the JSON shape Ollama expects:

```json
{
  "type": "function",
  "function": {
    "name": "search_documents",
    "description": "...",
    "parameters": { /* JSON Schema */ }
  }
}
```

This is what gets sent to `/api/chat` on Ollama.

## Argument validation (FR-23)

The orchestrator uses `jsonschema.Draft7Validator` (`app/services/orchestrator/tool_call.py`) to validate the LLM's tool-call arguments before execution. On failure it builds a corrective system note and asks the LLM once more (in `run_turn`). The streaming path skips the explicit retry and falls back to deterministic keyword extraction if validation fails twice in a row.

See [orchestrator.md](orchestrator.md) for the full validate → retry → fallback story.

## Tool-call audit

Each tool invocation produces a `tool_calls` row (id, message_id, user_id, tool_id, tool_name, arguments, result, status, latency_ms). This is for compliance and debugging; not exposed in the API in Phase 1.

# Orchestrator (chat turn) architecture

The orchestrator is the per-turn agent loop. It assembles a budgeted prompt, calls the LLM, optionally executes a tool call, and streams the final answer.

## Files

| Path | Role |
|---|---|
| `app/services/orchestrator/agent.py` | `run_turn()` (non-streaming) and `stream_turn()` (SSE) |
| `app/services/orchestrator/llm_client.py` | Wraps the Ollama client; exposes `complete()` and `stream()`. |
| `app/services/orchestrator/prompter.py` | Assembles the budgeted prompt; extracts citations from the answer. |
| `app/services/orchestrator/tool_call.py` | Validates arguments, builds the corrective note, computes the keyword fallback. |
| `app/services/retrieval/search.py` | The retrieval entry point used by both the built-in tool and the orchestrator. |

## High-level flow

```
                        ┌──────────────────────┐
                        │ POST /api/chat/stream│
                        └──────────┬───────────┘
                                   │
                                   ▼
                       ┌───────────────────────┐
                       │  agent.stream_turn()  │
                       └──────────┬────────────┘
                                  │
              ┌───────────────────┼────────────────────┐
              │                   │                    │
              ▼                   ▼                    ▼
   _ensure_conversation()  _load_history()   fallback_keywords() →
                                                   retrieval.retrieve()
                                                          │
                                                          ▼
                                                  initial_chunks
                                                          │
                          ┌───────────────────────────────┘
                          ▼
                  build_prompt(...)
                          │
                          ▼
                  llm.complete()  ◀── first pass (non-streaming)
                          │
                          ▼
                  tool_call?  ──── no ──▶ stream first.text
                          │
                          │ yes
                          ▼
                  _execute_tool_call()
                          │
                          ├── ok ──▶ rebuild prompt with tool result
                          │           │
                          │           ▼
                          │      llm.stream()  ◀── final answer
                          │
                          └── error ──▶ emit error, optionally fall back
                                                 (deterministic keywords)
                          │
                          ▼
                  _persist_message(role="assistant")
                  extract_citations()
                  emit RUN_FINISHED
```

## Two paths: `run_turn` vs `stream_turn`

`run_turn` is the non-streaming variant exposed at `POST /api/chat`. It runs the same loop but returns the final answer as JSON. Both paths share `_ensure_conversation`, `_load_history`, `_execute_tool_call`, and `_persist_message`.

`stream_turn` is what the frontend uses (via `POST /api/chat/stream`). It yields SSE events:

1. `RUN_STARTED` (immediately, before the LLM is called)
2. `TOOL_CALL_START` → `TOOL_CALL_ARGS` → `TOOL_CALL_END` (if the LLM called a tool)
3. `TEXT_MESSAGE_START` → many `TEXT_MESSAGE_CONTENT` deltas
4. `TEXT_MESSAGE_END` (with `citations` and `used_tools`)
5. `RUN_FINISHED` (or `RUN_ERROR`)

## The agent loop in detail

`stream_turn()` is the canonical implementation. Steps:

### 1. Conversation + history

```python
conv = await _ensure_conversation(session, user_id, conversation_id, title_seed=message)
history = await _load_history(session, user_id, conv.id, limit=16)
```

`_ensure_conversation` either loads the existing conversation (verifying ownership) or creates a new one with a title seeded from the first user message.

`_load_history` reads the most recent 16 user/assistant turns in chronological order.

### 2. Persist the user message

```python
_persist_message(session, user_id, conv.id, role="user", content=message)
await session.flush()
```

The user message is written before the run starts so the chat list re-fetch from the frontend reflects it.

### 3. Tool schemas

```python
tool_schemas = await tool_registry.select_subset(session, requested=tool_subset)
```

`select_subset` reads the cached tool snapshot from Redis, optionally filtered by `tool_subset` (an allow-list of tool names).

### 4. Up-front retrieval (heuristic)

```python
kws = fallback_keywords(message, top_k=6)
if kws:
    initial_chunks = await retrieval.retrieve(
        session=session, user_id=user_id, keywords=kws, top_k=4
    )
```

We pre-fetch a small set of chunks so the first LLM call already has context. This is cheap because `fallback_keywords` is deterministic and the result is cache-keyed on `(user_id, normalized_query)`.

### 5. Budgeted prompt

```python
built = build_prompt(
    query=message, chunks=initial_chunks, history=history, tools=tool_schemas
)
```

`build_prompt` fits everything into `ATHENA_TOKEN_BUDGET` (default 3,000). See [architecture/token-budget.md](token-budget.md) for the breakdown.

### 6. First LLM pass (non-streaming)

```python
first = await llm.complete(messages=built.messages, tools=built.tools)
```

We do the first call non-streaming so we can cheaply detect a tool call. Streaming + incremental JSON parse for tool calls is overkill for Phase 1.

### 7. Tool round-trip

```python
if first.tool_call and first.tool_call.get("name"):
    yield sse.tool_call_start(...)
    yield sse.tool_call_args(...)
    result, status, audit = await _execute_tool_call(...)
    yield sse.tool_call_end(...)
```

`_execute_tool_call` does:

1. Coerce the LLM's raw arguments (string, dict, or other) into a dict.
2. Look up the tool by name.
3. Validate arguments against the tool's JSON schema (Draft-07).
4. If valid, dispatch to the right handler (`internal`, `http`, `mcp`).
5. If invalid, return `status="error"`.

The orchestrator then:

- On `ok` or `fallback`: append the tool result to the message list and re-ask the LLM (streaming this time).
- On `error`: stream the original first-pass answer plus an error note.

#### FR-23: validate → retry → fallback (NFR-10)

For invalid tool calls (the LLM emitted wrong arguments), the *non-streaming* `run_turn` does:

1. Validate the LLM's tool call arguments.
2. If invalid, build a corrective system note and ask the LLM once more.
3. If still invalid, run a deterministic `fallback_keywords(message, top_k=6)` and call the tool again.
4. If the fallback also fails, return a graceful error to the user.

The streaming path skips the explicit retry and relies on the deterministic fallback so the user always gets a streamed answer.

### 8. Stream the final answer

```python
async for ev in llm.stream(messages=built.messages, tools=built.tools):
    yield sse.text_message_content(msg_id, ev["delta"])
```

The Ollama client (`llm_client.py`) is an `httpx`-based async wrapper around `/api/chat`. It parses NDJSON-style streaming responses into deltas.

### 9. Cite + persist + finish

```python
citations = extract_citations(text_to_stream, final_chunks)
_persist_message(session, ..., role="assistant", content=text_to_stream, citations=citations, used_tools=used_tools_log)
yield sse.text_message_end(msg_id, citations=citations, used_tools=used_tools_log)
yield sse.run_finished(run_id, finish_reason="stop")
```

`extract_citations` scans the answer for `[chunk:<uuid>]` substrings and maps them to the chunks we actually used.

## Built-in tool: `search_documents`

There is one built-in tool in Phase 1. It's registered in `infra/init.sql` and its handler resolves to `app.tools.builtin.search_documents:run` (the function is also aliased to `run`).

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

The orchestrator injects `user_id` and `session` into the arguments before calling, so the LLM never sees them in the schema.

## Why non-streaming first pass?

Streaming tool calls require an incremental JSON parser that can detect "the LLM has finished emitting arguments" mid-stream. With a 1.5B model and small budgets, the first pass is fast enough (typically < 1s) that the latency cost is small. The benefit is a much simpler control flow.

If we later move to a larger model, we can switch to streaming tool calls by feeding the SSE deltas to an incremental parser (e.g. `incremental-json-parser` or our own small state machine).

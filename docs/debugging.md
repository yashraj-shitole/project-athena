# Debugging

Quick recipes for the most common failure modes. Most of these are visible in the structured logs (`structlog` → JSON in prod, colorised in dev).

## Backend

### Enable verbose logging

```bash
ATHENA_LOG_LEVEL=DEBUG ATHENA_DEBUG=true uvicorn main:app --reload
```

You'll get pretty console logs and SQL echo (`ATHENA_DB_ECHO=true` for SQL).

### "Tool returned invalid args" repeatedly

`validate_arguments` is failing. The LLM emitted a tool call that doesn't match the JSON schema. Look for `tool.exec.error` in the logs. The corrective note + retry path is in `app/services/orchestrator/agent.py:run_turn`.

Common cause: the tool's `parameters` JSON schema is too restrictive. Test it in isolation with `jsonschema.Draft7Validator.check_schema()`.

### Retrieval returns nothing

```sql
-- in psql
SET app.current_user_id = '<your user id>';
SELECT count(*) FROM document_chunks;
SELECT count(*) FROM document_chunks WHERE content_tsv @@ websearch_to_tsquery('english', 'your query');
```

If the first returns 0, ingestion didn't run. If the second returns 0, your query doesn't match the indexed content — try a different query or lower `ATHENA_KEYWORD_MIN_SIM` (or check that the chunker is splitting paragraphs sensibly).

If both return non-zero, the LLM may not be calling `search_documents`. Look for `tool.exec.error` or a `RUN_FINISHED` with no `used_tools`.

### 401 immediately after login

Clock skew. The JWT is valid for 30 min; on the next request, `decode_token` fails. Check `date` on the server and the client.

If your reverse proxy is rewriting the `Authorization` header (it shouldn't), the backend can't see the bearer token. Test with `curl -H 'Authorization: Bearer ...'` directly against `:8000`.

### 413 on upload

File exceeds `ATHENA_UPLOAD_MAX_BYTES` (default 25 MB). Either raise the limit or split the document.

### 415 on upload

File extension not in `ATHENA_UPLOAD_ALLOWED_TYPES`. Default is `["csv","xlsx","pdf","doc","docx"]`. Add `txt`/`md`/`html` if you want prose files.

### Health check is "degraded"

`/health` reports each component's `ok` field. Pick the failing one and dig in:

- `db.ok = false` — Postgres is down, the connection string is wrong, or the pool is exhausted.
- `redis.ok = false` — Redis is down (the app should still work, just no caching).
- `llm.ok = false` — Ollama isn't running, the model isn't pulled, or `ATHENA_OLLAMA_URL` is wrong.

### SSE stream hangs

The backend's `stream_turn` is async, but if the underlying LLM never returns, the client never sees `RUN_FINISHED`. Cancel from the client (the `AbortController` will fire) and check Ollama.

### Slow ingestion

- 1.5k chunks * (1.2k tokens * 384-dim embedding) = 30+ seconds on CPU.
- Move to GPU-backed Ollama, or pre-warm the embedding model in the worker container.

## Frontend

### Stuck on "Loading…"

`useAuth` is waiting for `/auth/me` to return. Open DevTools → Network and look for a 401. The auth boundary should redirect you to `/login?next=…`.

If `/auth/me` is fine and you still see "Loading…", there's a React rendering bug — check the console for unhandled errors.

### "Failed to fetch"

Network failure, CORS rejection, or the backend is down. Check the Network tab. The error is normalised by `apiClient` to "Cannot reach the server. Check your connection." for the user; the raw message is in the console.

### Authenticated but backend says 401

- Token expired (30 min default). Use the refresh flow.
- Local backend time skew. Restart the backend and the SPA.
- The `Authorization` header isn't being sent. Check the request in DevTools.

### SSE stream never completes

Open the Network tab → the `/api/chat/stream` request → Response. The latest `data:` line should be `RUN_FINISHED` or `RUN_ERROR`. If you see only `RUN_STARTED` and then nothing, the LLM call is hanging.

### "Cannot find module '...apiClient.js'"

Usually a missing export. The full list of expected exports from `apiClient` is:

```js
export const AUTH_EVENT;
export function getToken();
export function setToken(token);
export function setRefreshToken(token);
export function setTokens(access, refresh);
export function getRefreshToken();
export function clearTokens();
export const apiClient;
```

## Postgres

### Inspect RLS

```sql
SELECT schemaname, tablename, rowsecurity
FROM pg_tables WHERE schemaname = 'public';
```

All user-owned tables should have `rowsecurity = t`.

### Verify the GUC

```sql
SHOW app.current_user_id;
```

If it's empty, the application didn't call `set_rls_user` on this transaction. Check that the route uses `get_user_db` (which sets it) and not `get_db` (which doesn't).

### Force-rebuild the HNSW index

```sql
REINDEX INDEX idx_chunks_embedding_hnsw;
```

Useful if you bulk-loaded chunks and the recall is poor.

## Redis

### Inspect cache keys

```bash
redis-cli KEYS "athena:*"
```

Should show `athena:cache:hits`, `athena:cache:misses`, `athena:search:<user_id>:<hash>`, `athena:tooldef:snapshot:v1`. If you see keys not prefixed with `athena:`, the namespace helper is being bypassed.

### Manually invalidate

```bash
redis-cli DEL "athena:tooldef:snapshot:v1"
redis-cli --scan --pattern "athena:search:*" | xargs -L 100 redis-cli DEL
```

The second line wipes every user's retrieval cache — use after a model or schema change.

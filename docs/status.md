# Phase 1 status

Phase 1 (MVP) is functional. This document maps every functional requirement to its implementation, and lists what's intentionally deferred to Phase 2.

## FR checklist (FR-01 … FR-39)

### Authentication (FR-01 … FR-04)

| FR | Title | Implementation | Tests |
|---|---|---|---|
| FR-01 | Create user | `POST /api/auth/register` (`app/api/auth.py`) | `test_security.py::test_hash_and_verify_password` |
| FR-02 | Login | `POST /api/auth/login` (OAuth2) + `/api/auth/login-json` | manual via curl |
| FR-03 | Refresh access token | `POST /api/auth/refresh` | `test_security.py::test_jwt_round_trip` |
| FR-04 | Current user | `GET /api/auth/me` | manual |

### Documents (FR-05 … FR-09)

| FR | Title | Implementation |
|---|---|---|
| FR-05 | Upload document | `POST /api/documents` — multipart, validates type + size, persists file. |
| FR-06 | Async ingestion | `_run_ingest` background task → `ingest_document` (extract → clean → chunk → embed → keywords → store). |
| FR-07 | List documents | `GET /api/documents` — paginated, status filter. |
| FR-08 | Single document | `GET /api/documents/{id}`. |
| FR-09 | Delete document | `DELETE /api/documents/{id}` — file + chunks + RLS-aware. |

### Retrieval (FR-10 … FR-21)

| FR | Title | Implementation |
|---|---|---|
| FR-10 | BM25 / tsvector search | `app/services/retrieval/lexical.py` + GIN index on `content_tsv`. |
| FR-11 | Vector search | `app/services/retrieval/vector.py` + HNSW index on `embedding`. |
| FR-12 | Embedding model | `all-MiniLM-L6-v2` (384-dim), `app/services/embedding.py`. |
| FR-13 | Chunking | `app/services/ingestion/chunker.py` — token-aware recursive. |
| FR-14 | Keywords | `app/services/ingestion/keywords.py` — TF-IDF + MMR. |
| FR-15 | Hybrid fusion | `app/services/retrieval/hybrid.py` — RRF. |
| FR-16 | Cache | `app/services/retrieval/search.py` + `app/core/cache.py`. |
| FR-17 | Short context budget (NFR-17) | `app/services/orchestrator/prompter.py` — see [architecture/token-budget.md](architecture/token-budget.md). |
| FR-18 | Citations | `extract_citations` parses `[chunk:<uuid>]` from the LLM's answer. |
| FR-19 | On-demand hybrid | `RETRIEVAL_HYBRID_THRESHOLD` — default behaviour. |
| FR-20 | Tool result shape | `search_documents` returns `[{chunk_id, document_id, document_name, page_number, score, snippet, keywords}]`. |
| FR-21 | Always-on hybrid | `RETRIEVAL_ALWAYS_HYBRID=true` (off by default in Phase 1). |

### Chat (FR-22 … FR-26)

| FR | Title | Implementation |
|---|---|---|
| FR-22 | Non-streaming chat | `POST /api/chat` → `agent.run_turn`. |
| FR-23 | Tool-call resilience | `validate → retry → fallback` in `tool_call.py` + `agent.py`. |
| FR-24 | Tool-call audit | `tool_calls` table records every invocation. |
| FR-25 | Citations in response | `ChatResponse.message.citations` populated by `extract_citations`. |
| FR-26 | Streaming chat (SSE) | `POST /api/chat/stream` — see [architecture/streaming.md](architecture/streaming.md). |

### Tools (FR-27 … FR-30)

| FR | Title | Implementation |
|---|---|---|
| FR-27 | Tool registry | `tools` table + `app/api/tools.py` (POST/GET/PATCH). |
| FR-28 | Enable / disable | `PATCH /api/tools/{id}?enabled=…`. |
| FR-29 | MCP discovery | `POST /api/tools/mcp/attach?server_url=…` (minimal JSON-RPC; full MCP is Phase 2). |
| FR-30 | Ollama-shaped tool list | `GET /api/tools/snapshot` — cached in Redis. |

### Conversations (FR-31 … FR-33)

| FR | Title | Implementation |
|---|---|---|
| FR-31 | CRUD conversations | `POST/GET/DELETE /api/chat/conversations` + `GET /api/chat/conversations/{id}`. |
| FR-32 | Persist messages | `messages` table populated by `agent._persist_message`. |
| FR-33 | Conversation-scoped history | `_load_history` reads the most recent 16 turns for the active conversation. |

### Ops (FR-34 … FR-39)

| FR | Title | Implementation |
|---|---|---|
| FR-34 | Cache TTL | `ATHENA_CACHE_TTL_SECONDS` (default 300). |
| FR-35 | Tool snapshot cache | `app/tools/registry.py` — `athena:tooldef:snapshot:v1`, invalidated on upsert/enable. |
| FR-36 | Cache hit/miss metrics | `GET /metrics` + `athena:cache:hits` / `athena:cache:misses` counters. |
| FR-37 | Health endpoint | `GET /health` — DB / Redis / LLM. |
| FR-38 | Per-user isolation | App-layer `WHERE user_id = :uid` + Postgres RLS GUC. |
| FR-39 | Active model info | `GET /model` — model, provider, base_url, budget, embedding. |

## Scope notes

- **FR-21 (always-on hybrid)** — implemented and gated by `ATHENA_RETRIEVAL_ALWAYS_HYBRID`; default Phase 1 behaviour is on-demand hybrid (vector when lexical top-1 is below threshold).
- **FR-29 (MCP)** — minimal JSON-RPC client shipped (`tools/list` and `tools/call`); full MCP spec (stdio transport, streaming notifications, sampling, roots) is Phase 2.
- **Tests** — unit tests for `text`, `security`, `tool_call`, `prompter`. Integration tests for Postgres/Redis/Ollama are gated behind `--run-integration`.
- **Observability** — structured JSON logs (`structlog`), `/health` / `/model` / `/metrics` endpoints, cache hit/miss counters. OpenTelemetry tracing is Phase 2.
- **Token storage** — JWTs in `localStorage` for Phase 1. httpOnly cookies in Phase 2.

## Phase 2 — top priorities

1. `httpOnly` cookie auth.
2. Streaming tool calls (latency win on tool-using turns).
3. OpenTelemetry + Prometheus.
4. Full MCP transport coverage.
5. Cross-encoder reranker.
6. Frontend test suite (Vitest + Playwright).

See [phase-2.md](phase-2.md) for the full list.

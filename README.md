# Project Athena

> AI assistant with tool-calling, document intelligence, and short-context-aware orchestration. Phase 1 (MVP).

Athena ingests CSV / XLSX / PDF / DOC / DOCX, indexes them with BM25 + sentence-transformer embeddings, and answers user questions by orchestrating a locally hosted **Qwen2.5-1.5B-Instruct** model. The orchestrator uses tool-calling, structured/cited responses, and a hard token budget suitable for small models.

## Architecture

```
┌──────────┐   HTTPS    ┌──────────────────┐
│ Frontend │ ─────────▶ │ FastAPI Backend  │
│ (Vite)   │ ◀─── SSE ─ │  /api (REST+SSE) │
└──────────┘            └────────┬─────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
   ┌─────────┐             ┌──────────┐              ┌─────────┐
   │Postgres │             │  Redis   │              │ Ollama  │
   │pgvector │             │  cache   │              │  LLM    │
   │ + BM25  │             │          │              │ runtime │
   └─────────┘             └──────────┘              └─────────┘
        ▲
        │
   ┌────┴────────┐
   │ Ingestion   │  (extract → clean → chunk → embed → keywords → index)
   │ worker      │
   └─────────────┘
```

## Repository layout

```
project-athena/
├── backend/                 FastAPI + SQLAlchemy async
│   ├── main.py              App entrypoint
│   ├── requirements.txt
│   ├── app/
│   │   ├── api/             REST routes (auth, docs, chat, tools, health)
│   │   ├── core/            config, db, security, cache, deps, logging
│   │   ├── models/          SQLAlchemy ORM models
│   │   ├── schemas/         Pydantic schemas
│   │   ├── services/
│   │   │   ├── embedding.py
│   │   │   ├── text.py
│   │   │   ├── ingestion/   extractors, chunker, keywords, store, pipeline
│   │   │   ├── retrieval/   lexical, vector, hybrid (RRF), rerank, search
│   │   │   ├── llm/         Ollama client, prompter, SSE streamer
│   │   │   ├── orchestrator/ llm_client, prompter, tool_call, agent
│   │   │   └── tools/       registry, builtin, mcp
│   │   └── tools/           Tool registry + builtin tools
│   └── tests/
├── frontend/                Vite + React + Zustand
│   └── src/
│       ├── pages/           Login, DocumentManager, ChatInterface
│       ├── components/      chat/*, documents/*
│       ├── hooks/           useAuth, useChatStream
│       ├── services/        API client + per-resource services
│       └── store/           Zustand stores
├── infra/
│   ├── docker-compose.yml   postgres, redis, ollama, api
│   ├── init.sql             DB schema with RLS + pgvector + tsvector
│   └── nginx.conf           reverse proxy
└── docs/                    design and reference docs
```

## Quick start (local Docker)

```bash
# 1. Start infra
cd infra
docker compose up -d postgres redis ollama
# 2. Pull model
docker exec -it athena-ollama ollama pull qwen2.5:1.5b-instruct
# 3. API
cd ../backend
python -m venv .venv && .venv\Scripts\activate      # Windows
# source .venv/bin/activate                          # macOS/Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# 4. Frontend
cd ../frontend
npm install
npm run dev
```

Open http://localhost:5173.

## API surface

All routes are mounted under `/api` except `/health`, `/model`, and `/metrics`.

| Method | Path                                | Description                          | FR     |
|--------|-------------------------------------|--------------------------------------|--------|
| POST   | `/api/auth/register`                | Create user                          | FR-01  |
| POST   | `/api/auth/login`                   | OAuth2 form login                    | FR-02  |
| POST   | `/api/auth/login-json`              | JSON login (SPA-friendly)            | FR-02  |
| POST   | `/api/auth/refresh`                 | Refresh access token                 | FR-03  |
| GET    | `/api/auth/me`                      | Current user                         | FR-04  |
| POST   | `/api/documents`                    | Upload document (multipart)          | FR-05  |
| GET    | `/api/documents`                    | List documents                       | FR-07  |
| GET    | `/api/documents/{id}`               | Single document                      | FR-08  |
| GET    | `/api/documents/{id}/chunks`        | Inspect indexed chunks               | —      |
| DELETE | `/api/documents/{id}`               | Delete document + chunks             | FR-09  |
| POST   | `/api/chat`                         | Non-streaming chat turn              | FR-22  |
| POST   | `/api/chat/stream`                  | SSE streaming turn                   | FR-26  |
| POST   | `/api/chat/conversations`           | Create conversation                  | FR-31  |
| GET    | `/api/chat/conversations`           | List conversations                   | FR-31  |
| GET    | `/api/chat/conversations/{id}`      | Get conversation messages            | FR-31  |
| DELETE | `/api/chat/conversations/{id}`      | Delete conversation                  | FR-31  |
| GET    | `/api/tools`                        | List registered tools                | FR-27  |
| POST   | `/api/tools`                        | Upsert a tool                        | FR-27  |
| PATCH  | `/api/tools/{id}?enabled=…`         | Enable/disable a tool                | FR-28  |
| POST   | `/api/tools/mcp/attach?server_url=` | Discover & register MCP tools        | FR-29  |
| GET    | `/api/tools/snapshot`               | Cached Ollama-shaped tool list       | FR-30  |
| POST   | `/api/tools/{id}/invoke`            | Ad-hoc tool invocation               | —      |
| GET    | `/health`                           | DB / Redis / LLM health              | FR-37  |
| GET    | `/model`                            | Active LLM + provider + budget       | FR-39  |
| GET    | `/metrics`                          | Cache hit/miss + tool counters       | FR-36  |

## Key design choices

- **Short context budget (3,000 tokens)** is enforced by truncation at prompter level — never by rejecting requests.
- **Tool calls** are JSON-validated; on malformed output the orchestrator retries once with a corrective system note, then falls back to deterministic keyword extraction.
- **Per-user isolation** is enforced at both the application layer (query filters) and the database layer (Postgres RLS via `SET LOCAL app.current_user_id`).
- **Provider / tool / persistence** are isolated behind narrow interfaces so any of them can be swapped without touching the others.
- **Streaming** uses SSE with AG-UI-shaped events (text deltas, tool-call lifecycle, run lifecycle).

## Configuration

All settings are read from env vars prefixed with `ATHENA_` (see `app/core/config.py`). Useful ones:

| Env                              | Default                                 | Notes                              |
|----------------------------------|-----------------------------------------|------------------------------------|
| `ATHENA_DATABASE_URL`            | `postgresql+asyncpg://athena:…/athena`  | Async SQLAlchemy URL               |
| `ATHENA_REDIS_URL`               | `redis://localhost:6379/0`              | Cache + counters                   |
| `ATHENA_OLLAMA_URL`              | `http://localhost:11434`                | LLM runtime                        |
| `ATHENA_OLLAMA_MODEL`            | `qwen2.5:1.5b-instruct`                 | Active model                       |
| `ATHENA_TOKEN_BUDGET`            | `3000`                                  | NFR-17 hard cap                    |
| `ATHENA_EMBEDDING_MODEL_NAME`    | `sentence-transformers/all-MiniLM-L6-v2`| 384-dim embeddings                 |
| `ATHENA_RETRIEVAL_ALWAYS_HYBRID` | `false`                                 | FR-21: RRF lexical+vector always   |
| `ATHENA_JWT_SECRET`              | `change-me-in-prod`                     | **Set in production**              |
| `ATHENA_CORS_ORIGINS`            | `["http://localhost:5173"]`             | Comma-separated list               |

## Status

Phase 1 MVP — functional. Implemented FR-01…39 with the following scope notes:

- **FR-21 (always-on hybrid)** — implemented and gated by `ATHENA_RETRIEVAL_ALWAYS_HYBRID`; default Phase 1 behaviour is on-demand hybrid (vector when lexical top-1 is below threshold).
- **FR-29 (MCP)** — minimal JSON-RPC client shipped; full MCP spec (stdio, streaming notifications) is Phase 2.
- **Tests** — unit tests for `text`, `security`, `tool_call`, `prompter`. Integration tests for Postgres/Redis/Ollama are gated behind `--run-integration`.
- **Observability** — structured JSON logs (structlog), `/health`/`/model`/`/metrics` endpoints, cache hit/miss counters. OpenTelemetry tracing is Phase 2.

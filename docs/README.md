# Project Athena — Documentation

> AI assistant with tool-calling, document intelligence, and short-context-aware orchestration. Phase 1 (MVP).

Athena ingests CSV / XLSX / PDF / DOC / DOCX / TXT / MD / HTML, indexes them with BM25 + sentence-transformer embeddings, and answers user questions by orchestrating a locally hosted **Qwen2.5-1.5B-Instruct** model. The orchestrator uses tool-calling, structured/cited responses, and a hard 3,000-token budget suitable for small models.

## High-level architecture

```
┌──────────┐   HTTPS    ┌──────────────────┐
│ Frontend │ ─────────▶ │ FastAPI Backend  │
│ (Vite)   │ ◀─── SSE ─ │  /api (REST+SSE) │
└──────────┘            └────────┬─────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
   ┌─────────┐             ┌──────────┊              ┌─────────┐
   │Postgres │             │  Redis   │              │ Ollama  │
   │pgvector │             │  cache   │              │  LLM    │
   │ + BM25  │             │          │              │ runtime │
   │ + RLS   │             │          │              └─────────┘
   └─────────┘             └──────────┘
        ▲
        │
   ┌────┴────────┐
   │ Ingestion   │  (extract → clean → chunk → embed → keywords → index)
   │ worker      │
   └─────────────┘
```

## Phase-1 status

Phase 1 MVP — functional. All FR-01…39 functional requirements are implemented. See `status.md` for the FR checklist and what's intentionally deferred to Phase 2.

## Where to start

| You want to … | Read |
|---|---|
| Run it locally | [quickstart.md](quickstart.md) |
| Understand the API | [api.md](api.md) |
| Understand the LLM orchestration | [architecture/orchestrator.md](architecture/orchestrator.md) |
| Understand retrieval (BM25 + vector + RRF) | [architecture/retrieval.md](architecture/retrieval.md) |
| Understand the ingestion pipeline | [architecture/ingestion.md](architecture/ingestion.md) |
| Understand the tool / MCP system | [architecture/tools.md](architecture/tools.md) |
| Understand the security model (RLS, JWT) | [architecture/security.md](architecture/security.md) |
| Understand the SSE streaming wire format | [architecture/streaming.md](architecture/streaming.md) |
| Understand the token budget enforcement | [architecture/token-budget.md](architecture/token-budget.md) |
| Configure env vars / deployment | [configuration.md](configuration.md) |
| Run tests | [testing.md](testing.md) |
| Develop / extend the frontend | [frontend.md](frontend.md) |
| Migrate to Phase 2 | [phase-2.md](phase-2.md) |
| Review a specific bug class | [debugging.md](debugging.md) |

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
│   │   │   └── tools/       (deprecated path) — see app/tools/
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
└── docs/                    this directory
```

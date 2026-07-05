# Database schema

The Postgres schema is defined in `infra/init.sql` and applied on first container start. It is idempotent — safe to re-run.

## ER overview

```
┌──────────┐         ┌──────────────┐         ┌──────────────────┐
│  users   │ 1───*  │  documents   │ 1───*  │ document_chunks   │
└──────────┘         └──────────────┘         └──────────────────┘
                                                        │
                                                        │ (FK)
                                                        ▼
┌──────────────┐         ┌──────────┐         ┌──────────────────┐
│ conversations│ 1───*   │ messages │ *───*   │   tool_calls     │
└──────────────┘         └──────────┘         └──────────────────┘
                                                        │ (FK)
                                                        ▼
                                                  ┌──────────┐
                                                  │  tools   │
                                                  └──────────┘
```

## Tables

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `email` | TEXT UNIQUE NOT NULL | |
| `password_hash` | TEXT NOT NULL | bcrypt |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

### `tools`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | TEXT NOT NULL | |
| `version` | INT NOT NULL DEFAULT 1 | bumped on every upsert/enable toggle |
| `description` | TEXT NOT NULL | |
| `parameters` | JSONB NOT NULL | JSON Schema (Draft 7) |
| `handler_type` | TEXT NOT NULL | `internal` \| `http` \| `mcp` |
| `handler_cfg` | JSONB NOT NULL DEFAULT '{}' | see `app/tools/registry.py` |
| `enabled` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `is_builtin` | BOOLEAN NOT NULL DEFAULT FALSE | |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

UNIQUE on `(name, version)`.

### `documents`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| `filename` | TEXT NOT NULL | original filename |
| `file_type` | TEXT NOT NULL | extension without dot |
| `storage_path` | TEXT NOT NULL | absolute path under `ATHENA_STORAGE_DIR` |
| `size_bytes` | BIGINT NOT NULL | |
| `page_count` | INT | set by ingestion for prose docs |
| `status` | TEXT NOT NULL DEFAULT 'uploaded' | `uploaded` → `processing` → `indexed` \| `failed` |
| `error_message` | TEXT | populated on failure |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

Indexes: `(user_id)`, `(user_id, status)`.

### `document_chunks`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `document_id` | UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE | |
| `user_id` | UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE | denormalised for RLS |
| `chunk_index` | INT NOT NULL | 0-based |
| `content` | TEXT NOT NULL | |
| `content_tsv` | tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED | indexed GIN |
| `embedding` | vector(384) | indexed HNSW |
| `keywords` | TEXT[] NOT NULL DEFAULT '{}' | indexed GIN |
| `page_number` | INT | for prose docs |
| `row_range` | INT4RANGE | for tabular docs |
| `char_start` | INT | span in the source |
| `char_end` | INT | span in the source |
| `metadata` | JSONB NOT NULL DEFAULT '{}' | sheet, row, etc. |
| `created_at` | TIMESTAMPTZ | |

Indexes:
- `(user_id, document_id)` — primary access pattern
- `GIN (content_tsv)` — lexical search
- `HNSW (embedding vector_cosine_ops)` — vector search
- `GIN (keywords)` — keyword filter

### `conversations`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE | |
| `title` | TEXT | first user message (truncated to 120) |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | bumped on every new message |

Index: `(user_id, updated_at DESC)` — supports the conversations list.

### `messages`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `conversation_id` | UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE | |
| `user_id` | UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE | denormalised for RLS |
| `role` | TEXT NOT NULL | `user` \| `assistant` \| `system` \| `tool` |
| `content` | TEXT NOT NULL | |
| `citations` | JSONB NOT NULL DEFAULT '[]' | the citation list |
| `used_tools` | JSONB NOT NULL DEFAULT '[]' | the tool audit |
| `created_at` | TIMESTAMPTZ | |
| `seq` | BIGSERIAL | monotonic per conversation |

Index: `(conversation_id, seq)`.

### `tool_calls`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `message_id` | UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE | |
| `user_id` | UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE | denormalised |
| `tool_id` | UUID REFERENCES tools(id) ON DELETE SET NULL | may be null if the tool was deleted |
| `tool_name` | TEXT NOT NULL | denormalised for auditing |
| `arguments` | JSONB NOT NULL | what the LLM sent |
| `result` | JSONB | what the handler returned |
| `status` | TEXT NOT NULL | `ok` \| `error` \| `fallback` |
| `latency_ms` | INT | |
| `created_at` | TIMESTAMPTZ | |

Index: `(message_id)`.

## Row-Level Security

Every user-owned table has RLS enabled. A single helper function reads the GUC:

```sql
CREATE OR REPLACE FUNCTION athena_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.current_user_id', TRUE), '')::uuid
$$;
```

Each table has a `USING (user_id = athena_current_user())` policy. The application sets the GUC via `SET LOCAL app.current_user_id = :uid` at the start of every user-scoped request.

Tables **without** RLS: `users` (auth-gated), `tools` (global registry).

## Extensions

- `vector` — pgvector for `vector(N)` and HNSW.
- `pg_trgm` — enabled for future trigram fallback; not used in Phase 1.

## Migrations

`init.sql` is the only migration in Phase 1. For Phase 2 we recommend switching to Alembic (already in `requirements.txt`) — see [phase-2.md](../phase-2.md).

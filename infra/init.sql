-- =====================================================================
-- Project Athena - Database Schema (Phase 1, MVP)
-- Idempotent. Designed for: pgvector + BM25/tsvector, with RLS isolation.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------
-- USERS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    token_version INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- TOOLS REGISTRY
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tools (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    version      INT  NOT NULL DEFAULT 1,
    description  TEXT NOT NULL,
    parameters   JSONB NOT NULL,           -- JSON schema
    handler_type TEXT NOT NULL,            -- 'internal' | 'http' | 'mcp'
    handler_cfg  JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    is_builtin   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

-- ---------------------------------------------------------------------
-- DOCUMENTS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    file_type     TEXT NOT NULL,           -- csv, xlsx, pdf, doc, docx
    storage_path  TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL,
    page_count    INT,
    status        TEXT NOT NULL DEFAULT 'uploaded',
                  -- 'uploaded' | 'processing' | 'indexed' | 'failed'
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_user      ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_user_stat ON documents(user_id, status);

-- ---------------------------------------------------------------------
-- DOCUMENT CHUNKS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    chunk_index     INT  NOT NULL,
    content         TEXT NOT NULL,
    content_tsv     tsvector GENERATED ALWAYS AS
                       (to_tsvector('english', content)) STORED,
    embedding       vector(384),
    keywords        TEXT[] NOT NULL DEFAULT '{}',
    page_number     INT,
    row_range       INT4RANGE,
    char_start      INT,
    char_end        INT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chunks_user_doc        ON document_chunks(user_id, document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv            ON document_chunks USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON document_chunks USING HNSW (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_keywords       ON document_chunks USING GIN (keywords);

-- ---------------------------------------------------------------------
-- CONVERSATIONS & MESSAGES
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,                 -- 'user' | 'assistant' | 'system' | 'tool'
    content         TEXT NOT NULL,
    citations       JSONB NOT NULL DEFAULT '[]'::jsonb,
    used_tools      JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    seq             BIGSERIAL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, seq);

-- ---------------------------------------------------------------------
-- TOOL CALL AUDIT
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_calls (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id   UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    tool_id      UUID REFERENCES tools(id) ON DELETE SET NULL,
    tool_name    TEXT NOT NULL,
    arguments    JSONB NOT NULL,
    result       JSONB,
    status       TEXT NOT NULL,                -- 'ok' | 'error' | 'fallback'
    latency_ms   INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_toolcalls_msg ON tool_calls(message_id);

-- ---------------------------------------------------------------------
-- ROW-LEVEL SECURITY (defense-in-depth isolation per NFR-07)
-- The application sets: SET app.current_user_id = '<uuid>';
--
-- FORCE ROW LEVEL SECURITY is applied so the table owner (the `athena`
-- role that runs init.sql and owns these tables) is ALSO subject to the
-- policies — otherwise the app, connecting as the owner, would bypass
-- RLS entirely and isolation would rest solely on app-layer WHERE
-- clauses. Policies carry a WITH CHECK so INSERTs/UPDATEs that would
-- write a foreign user_id are rejected at the DB, not just filtered on
-- read.
-- ---------------------------------------------------------------------
ALTER TABLE documents        ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks  ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations    ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages         ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_calls       ENABLE ROW LEVEL SECURITY;

ALTER TABLE documents        FORCE ROW LEVEL SECURITY;
ALTER TABLE document_chunks  FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations    FORCE ROW LEVEL SECURITY;
ALTER TABLE messages         FORCE ROW LEVEL SECURITY;
ALTER TABLE tool_calls       FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION athena_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.current_user_id', TRUE), '')::uuid
$$;

DROP POLICY IF EXISTS docs_iso   ON documents;
DROP POLICY IF EXISTS chunks_iso ON document_chunks;
DROP POLICY IF EXISTS conv_iso   ON conversations;
DROP POLICY IF EXISTS msg_iso    ON messages;
DROP POLICY IF EXISTS tcall_iso  ON tool_calls;

CREATE POLICY docs_iso   ON documents        USING (user_id = athena_current_user()) WITH CHECK (user_id = athena_current_user());
CREATE POLICY chunks_iso ON document_chunks  USING (user_id = athena_current_user()) WITH CHECK (user_id = athena_current_user());
CREATE POLICY conv_iso   ON conversations    USING (user_id = athena_current_user()) WITH CHECK (user_id = athena_current_user());
CREATE POLICY msg_iso    ON messages         USING (user_id = athena_current_user()) WITH CHECK (user_id = athena_current_user());
CREATE POLICY tcall_iso  ON tool_calls       USING (user_id = athena_current_user()) WITH CHECK (user_id = athena_current_user());

-- ---------------------------------------------------------------------
-- SEED: built-in search_documents tool
-- ---------------------------------------------------------------------
INSERT INTO tools (name, version, description, parameters, handler_type, handler_cfg, enabled, is_builtin)
VALUES (
    'search_documents',
    1,
    'Search the user''s indexed documents for chunks relevant to the given keywords. Returns a ranked list of chunk snippets with document/chunk IDs for citation.',
    '{
       "type": "object",
       "properties": {
         "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 16},
         "top_k":   {"type": "integer", "minimum": 1, "maximum": 16, "default": 4}
       },
       "required": ["keywords"]
     }'::jsonb,
    'internal',
    '{"impl": "app.tools.builtin.search_documents:run"}'::jsonb,
    TRUE,
    TRUE
)
ON CONFLICT (name, version) DO NOTHING;

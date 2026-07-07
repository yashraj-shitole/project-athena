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
    -- Progress + post-completion metadata (FR-4 ingest status).
    current_stage     TEXT,                -- 'extracting' | 'chunking' | 'embedding' | 'indexing' | 'completed' | 'failed'
    stage_progress    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {stage: 0..100}
    chunk_count       INT,
    embedding_model   TEXT,
    started_at        TIMESTAMPTZ,
    processed_at      TIMESTAMPTZ,
    processing_time_ms INT,
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

-- =====================================================================
-- EXTERNAL MODEL CONNECTORS (Phase 2)
-- Idempotent. Adds a user-scoped connector registry with RLS, an
-- append-only audit log, and a per-request usage table. API keys are
-- stored encrypted (Fernet) as BYTEA; only an `api_key_preview` string
-- is ever returned to the API client.
-- =====================================================================

-- ---------------------------------------------------------------------
-- MESSAGES: track which connector + model produced each assistant row
-- ---------------------------------------------------------------------
ALTER TABLE messages ADD COLUMN IF NOT EXISTS connector_id UUID;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS model        TEXT;

-- ---------------------------------------------------------------------
-- MODEL CONNECTORS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_connectors (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    provider               TEXT NOT NULL,                      -- openai_compat | anthropic | gemini | azure_openai | ollama | custom
    base_url               TEXT NOT NULL,
    api_key_enc            BYTEA,                              -- Fernet ciphertext; NULL when no key needed
    api_key_preview        TEXT,                               -- e.g. "sk-…1234" for UI
    auth_type              TEXT NOT NULL DEFAULT 'bearer',     -- bearer | header | basic | none | oauth
    auth_header_name       TEXT,
    organization_id        TEXT,
    project_id             TEXT,
    api_version            TEXT,
    custom_headers         JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_model          TEXT NOT NULL,
    models                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    capabilities           JSONB NOT NULL DEFAULT '{}'::jsonb,
    settings               JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    is_default             BOOLEAN NOT NULL DEFAULT FALSE,
    is_admin               BOOLEAN NOT NULL DEFAULT FALSE,
    group_name             TEXT,
    tags                   TEXT[] NOT NULL DEFAULT '{}'::text[],
    is_favorite            BOOLEAN NOT NULL DEFAULT FALSE,
    last_health            TEXT,                               -- online | offline | auth_failed | rate_limited | slow | unknown
    last_health_at         TIMESTAMPTZ,
    last_health_latency_ms INT,
    consecutive_failures   INT NOT NULL DEFAULT 0,
    discovered_models      JSONB NOT NULL DEFAULT '[]'::jsonb,
    discovered_at          TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at             TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_connectors_user      ON model_connectors(user_id);
CREATE INDEX IF NOT EXISTS idx_connectors_enabled   ON model_connectors(is_enabled);
CREATE INDEX IF NOT EXISTS idx_connectors_provider  ON model_connectors(provider);
-- Name unique per user, but only among NON-deleted rows. A partial
-- unique index is the standard idiom for "soft-delete uniqueness".
CREATE UNIQUE INDEX IF NOT EXISTS uq_connectors_user_name_live
    ON model_connectors(user_id, name) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- CONNECTOR AUDIT LOG (append-only)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS connector_audit_log (
    id               BIGSERIAL PRIMARY KEY,
    connector_id     UUID NOT NULL REFERENCES model_connectors(id) ON DELETE CASCADE,
    user_id          UUID NOT NULL REFERENCES users(id)          ON DELETE CASCADE,
    action           TEXT NOT NULL,                 -- create | update | delete | set_default | test | refresh_models | clone
    before_redacted  JSONB,
    after_redacted   JSONB,
    ip               TEXT,
    user_agent       TEXT,
    at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_connector_at ON connector_audit_log(connector_id, at);

-- ---------------------------------------------------------------------
-- CONNECTOR USAGE (one row per request)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS connector_usage (
    id                BIGSERIAL PRIMARY KEY,
    connector_id      UUID NOT NULL REFERENCES model_connectors(id) ON DELETE CASCADE,
    user_id           UUID NOT NULL REFERENCES users(id)          ON DELETE CASCADE,
    model             TEXT NOT NULL,
    prompt_tokens     INT  NOT NULL DEFAULT 0,
    completion_tokens INT  NOT NULL DEFAULT 0,
    latency_ms        INT  NOT NULL DEFAULT 0,
    status            TEXT NOT NULL,                  -- ok | error | timeout | rate_limited | auth_failed | cancelled | stream_interrupted
    error_class       TEXT,
    cost_estimate     TEXT NOT NULL DEFAULT '0',      -- Decimal-as-text; load with Decimal()
    at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_usage_connector_at ON connector_usage(connector_id, at);
CREATE INDEX IF NOT EXISTS idx_usage_user_at      ON connector_usage(user_id, at);

-- ---------------------------------------------------------------------
-- RLS for the new tables
-- Connectors: a user sees their own rows + every admin-shared row.
-- Audit + usage: a user sees their own rows; admin tools override the
-- GUC to read across users.
-- ---------------------------------------------------------------------
ALTER TABLE model_connectors     ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_audit_log  ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_usage      ENABLE ROW LEVEL SECURITY;

ALTER TABLE model_connectors     FORCE ROW LEVEL SECURITY;
ALTER TABLE connector_audit_log  FORCE ROW LEVEL SECURITY;
ALTER TABLE connector_usage      FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS connectors_iso ON model_connectors;
DROP POLICY IF EXISTS audit_iso      ON connector_audit_log;
DROP POLICY IF EXISTS usage_iso      ON connector_usage;

-- Owners see their rows; everyone sees admin-shared rows.
CREATE POLICY connectors_iso ON model_connectors
    USING (user_id = athena_current_user() OR is_admin = TRUE)
    WITH CHECK (user_id = athena_current_user() OR is_admin = TRUE);
CREATE POLICY audit_iso ON connector_audit_log
    USING (user_id = athena_current_user())
    WITH CHECK (user_id = athena_current_user());
CREATE POLICY usage_iso ON connector_usage
    USING (user_id = athena_current_user())
    WITH CHECK (user_id = athena_current_user());

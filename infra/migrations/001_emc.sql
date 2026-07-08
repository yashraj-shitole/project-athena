-- =====================================================================
-- Migration 001 — External Model Connectors (Phase 2)
--
-- Brings a pre-existing athena-postgres volume up to the Phase-2
-- schema. `infra/init.sql` is mounted at
-- /docker-entrypoint-initdb.d/00_init.sql and only runs on the first
-- boot of a fresh pgdata volume. When init.sql is extended after the
-- volume is already in use, the new DDL never runs. This file
-- carries the EMC section of init.sql so it can be re-applied
-- against a live DB.
--
-- Every statement is idempotent (IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS / DROP POLICY IF EXISTS + CREATE POLICY), so re-running
-- this file is safe.
--
-- Apply with:
--   docker exec -i athena-postgres psql -U athena -d athena \
--     < infra/migrations/001_emc.sql
--
-- See docs/bugfix/emc-migration.md for the original incident.
-- =====================================================================

-- ---------------------------------------------------------------------
-- MESSAGES: track which connector + model produced each assistant row
-- ---------------------------------------------------------------------
ALTER TABLE messages ADD COLUMN IF NOT EXISTS connector_id UUID;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS model        TEXT;

-- ---------------------------------------------------------------------
-- Helper function: admin predicate.
-- Defined here (as well as in init.sql) so the EMC policies can be
-- created on databases that pre-date the H-2 init.sql change. The
-- definition is identical to the one in init.sql and is
-- CREATE OR REPLACE so re-running is a no-op.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION athena_is_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(NULLIF(current_setting('app.is_admin', TRUE), ''), 'false')::boolean
$$;

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
    -- JSONB (not ARRAY) so the Python ORM can use a single column
    -- type that round-trips on both Postgres and the SQLite
    -- in-memory backend used by the unit-test suite. See
    -- ``backend/app/models/connector.py::ModelConnector.tags``.
    tags                   JSONB NOT NULL DEFAULT '[]'::jsonb,
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

-- H-2 (High) — split the connector visibility policy.
--
-- The previous policy was a single ``connectors_iso`` with
-- ``USING (user_id = me OR is_admin = TRUE)``. That meant
-- *any* row with ``is_admin=TRUE`` was globally visible — and
-- the route layer accepted ``is_admin=True`` from a non-admin
-- caller, which is C-2 (Critical). The route guard in
-- ``app/api/connectors.py`` blocks the create-time promotion,
-- but we want defense in depth at the database layer too.
--
-- The new design: two additive policies.
--
-- * ``connectors_owner`` — the owner sees their row, and the
--   owner can write (INSERT/UPDATE) only their own row. This
--   policy is sufficient to *block* the C-2 escalation: even
--   if a non-admin somehow wrote ``is_admin=true`` to a row,
--   the policy's ``WITH CHECK`` clause refuses to allow it
--   when ``athena_is_admin()`` is false.
-- * ``connectors_admin`` — a caller that is admin (per
--   ``athena_is_admin()``) sees admin-shared rows; a non-admin
--   caller sees no such rows. The ``WITH CHECK`` clause means
--   only an admin can *write* ``is_admin=true`` to a row.
--
-- The end result: a non-admin can never SELECT or WRITE a row
-- with ``is_admin=true``, regardless of what the application
-- layer does.
DROP POLICY IF EXISTS connectors_owner ON model_connectors;
DROP POLICY IF EXISTS connectors_admin ON model_connectors;
CREATE POLICY connectors_owner ON model_connectors
    USING (user_id = athena_current_user())
    WITH CHECK (user_id = athena_current_user() AND NOT is_admin);
CREATE POLICY connectors_admin ON model_connectors
    USING (is_admin = TRUE AND athena_is_admin())
    WITH CHECK (is_admin = TRUE AND athena_is_admin());
CREATE POLICY audit_iso ON connector_audit_log
    USING (user_id = athena_current_user())
    WITH CHECK (user_id = athena_current_user());
CREATE POLICY usage_iso ON connector_usage
    USING (user_id = athena_current_user())
    WITH CHECK (user_id = athena_current_user());

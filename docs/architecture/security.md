# Security model

Defense in depth: app-layer filters + Postgres RLS + JWT.

## Authentication: JWT (FR-01…04)

- Passwords are hashed with bcrypt via the `bcrypt` library (truncated to the 72-byte bcrypt limit before hashing).
- Access tokens are HS256 JWTs (`PyJWT`) with `sub=user_id`, `type="access"`, 30 min TTL.
- Refresh tokens have `type="refresh"`, 14 day TTL.
- The signing secret is `ATHENA_JWT_SECRET` — **set to a long random string in production**.
- `GET /api/auth/me` returns the current user (auth required).
- `POST /api/auth/refresh` exchanges a refresh token for a new access token.

The frontend stores the access and refresh tokens in `localStorage` under `athena_token` / `athena_refresh`. **Known Phase 1 trade-off**: this is XSS-vulnerable. The migration to `httpOnly` cookies is a Phase 2 item.

## Authorization: per-user filtering

Every API route that touches a user-owned table takes `CurrentUserId` as a dependency. Handlers always include `user_id == :uid` in their queries:

```python
res = await session.execute(
    select(Document).where(
        Document.id == doc_id, Document.user_id == user_id
    )
)
```

## Defense-in-depth: Row-Level Security (RLS)

The Postgres schema in `infra/init.sql` enables RLS on every per-user table and defines a policy:

```sql
CREATE POLICY docs_iso ON documents
    USING (user_id = athena_current_user());
```

`athena_current_user()` is a stable SQL function that reads the session-level GUC `app.current_user_id`:

```sql
CREATE OR REPLACE FUNCTION athena_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.current_user_id', TRUE), '')::uuid
$$;
```

The application sets the GUC at the start of every request via `core.database.set_rls_user`:

```python
await session.execute(
    text("SET LOCAL app.current_user_id = :uid"),
    {"uid": str(user_id)},
)
```

`SET LOCAL` is scoped to the current transaction and is reset on `COMMIT`/`ROLLBACK`. The result: even if a handler forgets a `WHERE user_id = :uid` clause, the query will return zero rows because the RLS policy filters them out.

### Tables with RLS

- `documents`
- `document_chunks`
- `conversations`
- `messages`
- `tool_calls`

The `users` and `tools` tables do *not* have RLS — `users` is keyed by the JWT subject and `tools` is a global registry (auth is sufficient).

### Verification

You can test RLS by:

```sql
SET app.current_user_id = '00000000-0000-0000-0000-000000000000';
SELECT count(*) FROM documents;   -- returns 0
RESET app.current_user_id;
SELECT count(*) FROM documents;   -- returns the actual count
```

## CORS

`ATHENA_CORS_ORIGINS` (default `["http://localhost:5173"]`) is the list of allowed origins. `allow_credentials=True` and `allow_methods/headers="*"`.

In production, set this to the actual frontend domain (e.g. `["https://athena.example.com"]`). Do *not* use `"*"` with credentials — browsers reject it.

## Request validation

- Pydantic v2 models (`app/schemas/`) validate every request body and response.
- `OAuth2PasswordRequestForm` for the `/auth/login` form path.
- Pydantic validators on settings (`app/core/config.py`).
- File upload: `ATHENA_UPLOAD_MAX_BYTES` cap; `ATHENA_UPLOAD_ALLOWED_TYPES` whitelist. Anything else returns 415.
- The orchestrator validates LLM tool-call arguments with `jsonschema.Draft7Validator`.

## Caching and the cache prefix

Retrieval results are cached per-user in Redis. Cache keys include the user ID, so users cannot see each other's cached results. Cache invalidation on document upload/delete ensures a stale corpus cannot bleed into answers.

## What is *not* in scope for Phase 1

- Rate limiting (per-user, per-IP). Recommended for Phase 2: a token-bucket middleware in front of the FastAPI app.
- CSRF: the API uses bearer tokens, not cookies. CSRF protection is not needed.
- Account lockout / brute-force protection. Use a reverse-proxy with fail2ban or move to a managed identity provider in Phase 2.
- Audit log of every API call. Today the structured logs (`structlog`) capture all tool calls, retriever calls, and ingestion events; HTTP-level access logs are the reverse proxy's job.
- Field-level encryption of stored documents. Documents are stored on disk under `ATHENA_STORAGE_DIR`; encrypt the volume if you need at-rest encryption.

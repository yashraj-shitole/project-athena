# Configuration

All settings are read from environment variables prefixed `ATHENA_`. They are declared in `backend/app/core/config.py` as a `pydantic-settings` `BaseSettings` model.

A `.env` file at the backend root is also picked up.

## Quick reference

| Env var | Default | Description |
|---|---|---|
| `ATHENA_APP_NAME` | `"Project Athena"` | App display name. |
| `ATHENA_ENVIRONMENT` | `"dev"` | Free-form env label, emitted in startup log. |
| `ATHENA_LOG_LEVEL` | `"INFO"` | `structlog` level. |
| `ATHENA_DEBUG` | `false` | Verbose stack traces + SQL echo. |
| **Database** |||
| `ATHENA_DATABASE_URL` | `postgresql+asyncpg://athena:athena@localhost:5432/athena` | Async SQLAlchemy URL. |
| `ATHENA_DB_POOL_SIZE` | `10` | asyncpg pool size. |
| `ATHENA_DB_ECHO` | `false` | Echo SQL via SQLAlchemy. |
| **Redis** |||
| `ATHENA_REDIS_URL` | `redis://localhost:6379/0` | Cache + counter connection. |
| `ATHENA_CACHE_TTL_SECONDS` | `300` | Default TTL for retrieval cache entries. |
| **LLM** |||
| `ATHENA_OLLAMA_URL` | `http://localhost:11434` | Ollama-compatible base URL. |
| `ATHENA_OLLAMA_MODEL` | `qwen2.5:1.5b-instruct` | Active model. |
| `ATHENA_OLLAMA_TIMEOUT` | `60.0` | Per-request timeout (seconds). |
| **Token budget (NFR-17)** |||
| `ATHENA_TOKEN_BUDGET` | `3000` | Hard total cap (NFR-17). |
| `ATHENA_SYSTEM_PROMPT_RESERVE` | `350` | Reserved for the system prompt. |
| `ATHENA_TOOL_DEF_RESERVE` | `600` | Reserved for tool schemas. |
| `ATHENA_HISTORY_RESERVE` | `800` | Reserved for conversation history. |
| `ATHENA_CHUNK_RESERVE` | `1000` | Reserved for context chunks. |
| `ATHENA_ANSWER_RESERVE` | `250` | Reserved for the LLM's answer (advisory; not directly enforced in Phase 1). |
| **Storage** |||
| `ATHENA_STORAGE_DIR` | `"./storage"` | Where uploaded files are written. |
| `ATHENA_UPLOAD_MAX_BYTES` | `26214400` (25 MB) | Per-file cap. |
| `ATHENA_UPLOAD_ALLOWED_TYPES` | `["csv","xlsx","pdf","doc","docx"]` | Whitelist. |
| **Ingestion** |||
| `ATHENA_CHUNK_SIZE_TOKENS` | `300` | Target chunk size. |
| `ATHENA_CHUNK_OVERLAP_TOKENS` | `50` | Overlap between adjacent chunks. |
| `ATHENA_EMBEDDING_MODEL_NAME` | `"sentence-transformers/all-MiniLM-L6-v2"` | 384-dim. |
| `ATHENA_EMBEDDING_DIM` | `384` | Must match the model. |
| `ATHENA_KEYWORD_TOP_N` | `8` | Top-N keywords to attach per chunk. |
| `ATHENA_KEYWORD_MIN_SIM` | `0.15` | Cosine threshold for the keyword on-topic filter. |
| **Retrieval** |||
| `ATHENA_RETRIEVAL_TOP_K` | `4` | Default top-k. |
| `ATHENA_RETRIEVAL_HYBRID_THRESHOLD` | `0.05` | Lexical top-1 below this triggers vector re-rank. |
| `ATHENA_RETRIEVAL_ALWAYS_HYBRID` | `false` | When `true`, always RRF lexical+vector (FR-21). |
| **Auth** |||
| `ATHENA_JWT_SECRET` | `"change-me-in-prod"` | **Set in production.** |
| `ATHENA_JWT_ALGORITHM` | `"HS256"` | PyJWT algorithm. |
| `ATHENA_ACCESS_TOKEN_TTL_MIN` | `30` | |
| `ATHENA_REFRESH_TOKEN_TTL_DAYS` | `14` | |
| **CORS** |||
| `ATHENA_CORS_ORIGINS` | `["http://localhost:5173"]` | List of allowed origins. |
| **Cache namespaces** |||
| `ATHENA_CACHE_PREFIX_RETRIEVAL` | `"search"` | |
| `ATHENA_CACHE_PREFIX_TOOL_DEF` | `"tools"` | |
| **External Model Connectors** |||
| `ATHENA_CONNECTOR_KEY` | (none) | Fernet key for API-key encryption. **Required in prod**; the dev fallback is HKDF-derived from `ATHENA_JWT_SECRET` and emits a structlog warning on every decrypt. |
| `ATHENA_CONNECTOR_HEALTH_INTERVAL_S` | `60` | Health-probe tick interval. |
| `ATHENA_CONNECTOR_HEALTH_FAILURE_THRESHOLD` | `3` | Consecutive failures before auto-disable. |
| `ATHENA_CONNECTOR_HEALTH_MAX_PER_CYCLE` | `50` | Max rows probed per cycle. |

## UPPER_CASE aliases

The settings object also exposes each setting as an UPPER_CASE `@property` for backward compatibility with services that use the historical names (`EMBED_DIM`, `OLLAMA_BASE_URL`, `TOKEN_BUDGET_TOTAL`, etc.). These never appear in env — they're code-only.

## Verifying effective settings

```bash
curl http://localhost:8000/model
# → { "model": "...", "provider": "ollama", "base_url": "...",
#     "context_budget": 3000, "embedding_model": "...", "embedding_dim": 384 }
```

## Production checklist

- [ ] Set `ATHENA_JWT_SECRET` to a long random string (`openssl rand -hex 32`).
- [ ] Set `ATHENA_CONNECTOR_KEY` to a Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Required for at-rest encryption of connector API keys.
- [ ] Set `ATHENA_ENVIRONMENT=prod` (controls log verbosity in some paths).
- [ ] Restrict `ATHENA_CORS_ORIGINS` to the actual frontend domain.
- [ ] Set `ATHENA_UPLOAD_MAX_BYTES` appropriate to the available storage.
- [ ] Make sure `ATHENA_STORAGE_DIR` is on a durable volume (the Docker compose mounts `api_storage`).
- [ ] Confirm Postgres is using SSL (`?sslmode=require` appended to the URL).
- [ ] Set `ATHENA_LOG_LEVEL=WARNING` for less noise in prod.
- [ ] Enable HTTPS at the proxy layer (`infra/nginx.conf` shows the dev config).

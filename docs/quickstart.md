# Quickstart

This walks you from a clean checkout to a running app on `localhost`.

> **Prefer the scripts.** The repo-root automation layer does everything below
> in one command, with env validation and health waits:
> `.\build.ps1 ; .\docker-up.ps1` (PowerShell) or `./build.sh && ./docker-up.sh`
> (bash). See [development/DeveloperScripts.md](development/DeveloperScripts.md)
> for the full surface. The manual steps below remain useful for understanding
> what the scripts do, and for the local-backend (Option B/C) workflows.

## Prerequisites

| Tool   | Version | Notes |
|--------|---------|-------|
| Python | 3.11+   | 3.12 also works |
| Node   | 18+     | enforced via `engines` in `frontend/package.json` |
| Docker | 24+     | for `docker compose`; skip if you have local Postgres/Redis/Ollama |
| Ollama | 0.5+    | only needed if running the LLM outside Docker |

## Option A — full Docker stack (recommended)

This brings up Postgres + Redis + Ollama + the API + nginx (serving the built
SPA) in one command. The Ollama model is pulled automatically on first boot.

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

Then open <http://localhost:8080>.

The first start takes a few minutes (it pulls Docker images, the Python
deps, the embedding model, and `qwen2.5:1.5b-instruct`). Subsequent starts
are near-instant.

To check it's up:

```bash
curl -fsS http://localhost:8080/health
# → {"status":"ok","checks":{"db":{"ok":true,...},...}}
```

## Option B — local backend, Docker infra

Use this if you want to edit backend code with hot reload.

```bash
# 1. Start only infra
docker compose -f infra/docker-compose.yml up -d postgres redis ollama

# 2. Backend in a venv
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# 3. Frontend (Vite dev server with HMR, proxies /api → :8000)
cd ../frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

## Option C — local everything (no Docker)

You'll need:
- Postgres 16 with `vector` and `pg_trgm` extensions
- Redis 7
- Ollama running on `localhost:11434` with `qwen2.5:1.5b-instruct` pulled

Then set:
```bash
export ATHENA_DATABASE_URL=postgresql+asyncpg://athena:athena@localhost:5432/athena
export ATHENA_REDIS_URL=redis://localhost:6379/0
export ATHENA_OLLAMA_URL=http://localhost:11434
```

Apply the schema:
```bash
psql -U athena -d athena -f infra/init.sql
```

Then run the backend / frontend as in Option B.

## Smoke test

The base URL depends on the option you chose:
- **Option A (Docker stack):** `http://localhost:8080` (nginx serves the SPA and reverse-proxies the API)
- **Option B / C (local):** `http://localhost:8000` for the API, `http://localhost:5173` for the SPA

```bash
# Register a user
curl -X POST http://localhost:8080/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"hunter222"}'

# Log in
curl -X POST http://localhost:8080/api/auth/login-json \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"hunter222"}'

# Upload a file (use the access_token from the login response)
TOKEN="<paste access_token here>"
curl -X POST http://localhost:8080/api/documents \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./README.md"

# Ops endpoints
curl http://localhost:8080/health
curl http://localhost:8080/model
curl http://localhost:8080/metrics
```

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `init.sql` did not run | the Postgres volume was already initialised | `docker compose -f infra/docker-compose.yml down -v` and re-up |
| `relation "vector" does not exist` | pgvector image not used | we use `pgvector/pgvector:pg16`; don't switch to plain `postgres:16` |
| LLM calls hang | Ollama model not pulled | the `ollama-pull` init container does this on first start; check `docker logs athena-ollama-pull` |
| Frontend can't reach backend (Option B) | Vite proxy not configured | `vite.config.js` proxies `/api` → `:8000` |
| 401 immediately after login | clock skew on the dev box | JWT TTL is 30 min; refresh via `/api/auth/refresh` |
| nginx 502 Bad Gateway | api container not ready yet | wait 10s after `docker compose up`; check `docker logs athena-api` |

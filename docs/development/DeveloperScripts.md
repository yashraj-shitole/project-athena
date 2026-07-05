# Developer Scripts

Project Athena ships a one-command developer automation layer at the repo
root and in `goThrough/`. It wraps Docker Compose with environment validation,
health-check waits, colored output, and a consistent project name so build,
run, debug, watch, test, and maintenance all "just work".

> Platform: Windows PowerShell 7+ (`.ps1`) is the primary interface; macOS /
> Linux equivalents are provided for the three root entry points
> (`build.sh`, `docker-up.sh`, `test.sh`). All `.ps1` scripts require
> `#Requires -Version 7.0` and run under `Set-StrictMode -Version Latest`.
> The `.sh` scripts require **bash 4+** (macOS ships bash 3.2 by default —
> `brew install bash` and ensure Homebrew's bin dir is ahead of `/usr/bin`
> on `PATH`; the scripts check and fail fast with a clear message otherwise).

## TL;DR

```powershell
.\build.ps1                 # build all images
.\docker-up.ps1             # run the stack            -> http://localhost:8080
.\docker-up.ps1 -Debug      # hot reload + debugger    -> :8000 / :5678 / :5173
.\docker-up.ps1 -Debug -Watch
.\test.ps1                  # all tests
.\health.ps1                # verify everything
.\reset.ps1 -Volumes        # wipe and start over
```

## How it fits together

```
build.ps1 ─┐
docker-up.ps1 ─┤   (root entry points, thin where they delegate)
test.ps1 ─┤
debug-*.ps1 ─┤
clean/reset/logs/status/health.ps1 ─┘
            │
            ▼
   goThrough/_helpers.ps1   ← single source of truth
   (compose invocation, colored output, env validation, health polling)
            │
            ▼
   goThrough/*.ps1           ← canonical implementations
            │
            ▼
   docker compose -f infra/docker-compose.yml [-f infra/docker-compose.debug.yml] ...
```

- Every script forces `COMPOSE_PROJECT_NAME=athena` and the compose file sets
  `name: athena`, so volumes/networks are always `athena_pgdata`,
  `athena_default`, etc. — regardless of where you invoke from.
- Scripts always run from the repo root (helpers `cd` there).
- The base compose file is `infra/docker-compose.yml`; the debug override is
  `infra/docker-compose.debug.yml` (applied with `-Debug`).

## Service map

The spec's generic "ai-service" and "worker" don't exist as separate
containers here — the LLM orchestrator, tool-calling, and the ingestion
pipeline all live inside the single `api` service. The friendly aliases map
as follows (accepted by every `-Service` / `-Services` argument):

| Friendly name         | Compose service | Container        | Notes |
|-----------------------|-----------------|------------------|-------|
| `backend` / `api`     | `api`           | `athena-api`     | FastAPI runtime, built from Dockerfile target `api` |
| `frontend` / `web` / `nginx` | `nginx`  | `athena-nginx`   | Production SPA + reverse proxy, target `nginx` |
| `web-dev` / `vite` / `dev`   | `web-dev`| `athena-web-dev` | Vite HMR (debug profile), target `web-dev` |
| `db` / `postgres` / `database` | `postgres` | `athena-postgres` | pgvector |
| `cache` / `redis`    | `redis`         | `athena-redis`    | |
| `llm` / `ollama`     | `ollama`        | `athena-ollama`   | Prebuilt image (not built) |
| `ai-service` / `ai` / `worker` | `api`  | `athena-api`      | Folded into `api`; aliases build the `api` target |

`goThrough/build-ai-service.ps1` and `goThrough/build-worker.ps1` are real,
functional aliases that build the `api` target — they exist so a generic CI
matrix referencing those names works unchanged, and print a one-line note
explaining the consolidation.

## 1. Root build script

### `build.ps1` / `build.sh`

Builds all Docker images in dependency order (`api` → `nginx` [, `web-dev`]),
after validating prerequisites + environment and ensuring the Docker network
and named volumes exist. Pre-pulls the third-party infra images.

| Flag | Meaning |
|------|---------|
| `-Clean` / `--clean` | Remove existing `athena-*` images before building |
| `-NoCache` / `--no-cache` | Ignore the Docker layer cache |
| `-Service <name>` / `--service <name>` | Build only one service (alias accepted) |
| `-IncludeDev` / `--include-dev` | Also build the `web-dev` (Vite) image |
| `-Production` / `--production` | Enforce prod-grade env validation (reject placeholder secrets) |
| `-Verbose` / `--verbose` | `--progress=plain` build output |

```powershell
.\build.ps1
.\build.ps1 -Clean -NoCache -Verbose
.\build.ps1 -Service backend
.\build.ps1 -Service frontend -IncludeDev
```

## 2. Root startup script

### `docker-up.ps1` / `docker-up.sh`

Builds if needed, starts the stack detached, waits for health checks, prints
service URLs + container status, and (on failure) dumps the failing logs.

| Flag | Meaning |
|------|---------|
| `-Debug` / `--debug` | Use the debug override (hot reload + debugpy + HMR) |
| `-Watch` / `--watch` | Implies `-Debug`; follow logs in foreground (Ctrl-C keeps the stack up) |
| `-Services a,b` / `--services a,b` | Start only these services |
| `-Build` / `--build` | Rebuild before starting |
| `-NoCache` / `--no-cache` | With `-Build`, ignore cache |
| `-Detached:$false` / `--foreground` | Run in foreground instead of detached |
| `-Timeout <sec>` / `--timeout <sec>` | Health-check timeout (default 120) |

```powershell
.\docker-up.ps1                       # normal:  -> http://localhost:8080
.\docker-up.ps1 -Debug                # debug:   -> :8000 + :5678 + :5173
.\docker-up.ps1 -Debug -Watch         # watch:   follow logs, hot reload
.\docker-up.ps1 -Services backend,frontend
.\docker-up.ps1 -Build
```

Default service sets:
- **Normal**: `postgres redis ollama ollama-pull api nginx`
- **Debug**: `postgres redis ollama ollama-pull api web-dev` (nginx excluded;
  the Vite HMR server serves the frontend on `:5173`)

Data (`pgdata`, `ollama_data`, `api_storage`, uploads) is **preserved** across
all modes — nothing here runs `down -v`.

## 3. Docker debug support

`infra/docker-compose.debug.yml` is a Compose override applied on top of the
base file when you pass `-Debug`. It enables:

- **Backend hot reload**: `uvicorn --reload` watching `/app/backend/app`; the
  host `backend/` source is bind-mounted over the image's copy, so edits on
  the host hot-reload without a rebuild.
- **Backend debugger**: `debugpy` listens on `127.0.0.1:5678` (attach-anytime;
  it does NOT block waiting for a client, so the server runs normally even
  with no debugger attached). `debugpy` is installed lazily on first start —
  it is not pinned in `requirements.txt` because it is a debug-only dep.
- **Frontend HMR**: `web-dev` is promoted out of the `dev` profile (so a plain
  `up` brings it up); Vite serves sourcemaps in dev by default.
- **Optional Node inspector** on `127.0.0.1:9229` (for debugging Vite itself).
- **Verbose logging** (`ATHENA_DEBUG=true`, `ATHENA_LOG_LEVEL=DEBUG`,
  `ATHENA_DB_ECHO=true`).

Manual use:

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.debug.yml up
```

### Attaching the Python debugger (VS Code)

`debug-backend.ps1` / `debug-all.ps1` print this snippet; save it as a launch
configuration in `.vscode/launch.json`:

```json
{
  "name": "Attach to Athena API (docker)",
  "type": "debugpy",
  "request": "attach",
  "connect": { "host": "127.0.0.1", "port": 5678 },
  "pathMappings": [
    { "localRoot": "${workspaceFolder}/backend", "remoteRoot": "/app/backend" }
  ],
  "justMyCode": false
}
```

Start `.\debug-backend.ps1`, then *Run → Attach to Athena API (docker)*.
Set breakpoints in `backend/app/**`; they map to the mounted source.

## 4. Watch mode

```powershell
.\docker-up.ps1 -Debug -Watch
```

- Starts the debug stack detached, waits for health, prints URLs, then
  tails logs in the foreground.
- Backend edits reload via `uvicorn --reload`; frontend via Vite HMR.
- **Ctrl-C detaches the log view only** — containers keep running.
- Databases (`pgdata`), the pulled model (`ollama_data`), and uploaded files
  (`api_storage`) are preserved.

## 5. `goThrough/` scripts

Reusable PowerShell scripts (the canonical implementations the root scripts
delegate to). All share `goThrough/_helpers.ps1`.

| Script | Purpose |
|--------|---------|
| `build-all.ps1` | Build every image in dependency order |
| `build-backend.ps1` | Build the `api` image |
| `build-frontend.ps1` | Build the `nginx` (and optionally `web-dev`) image |
| `build-ai-service.ps1` | Alias: builds `api` (LLM orchestrator lives there) |
| `build-worker.ps1` | Alias: builds `api` (ingestion runs inline there) |
| `docker-up.ps1` | Canonical start (build-if-needed, up, wait, URLs) |
| `docker-down.ps1` | Stop + remove containers (`-Volumes` to wipe data) |
| `docker-restart.ps1` | Restart services (no rebuild) |
| `logs.ps1` | Tail logs, filter by service |
| `clean.ps1` | Remove build artifacts + prune dangling images/cache |
| `prune.ps1` | Deep disk cleanup (remove athena-* images, cache; `-Volumes` for volumes) |
| `debug.ps1` | Canonical debug launcher (full debug stack) |
| `shell-backend.ps1` | Interactive shell in the `api` container |
| `shell-db.ps1` | `psql` session in the `postgres` container |
| `migrate.ps1` | Re-apply `infra/init.sql` (idempotent) |
| `seed.ps1` | Register a demo user (+ optional sample doc upload) |

```powershell
.\goThrough\build-all.ps1 -NoCache -Verbose
.\goThrough\docker-up.ps1 -Debug
.\goThrough\docker-down.ps1 -Volumes
.\goThrough\logs.ps1 -Services api,nginx
.\goThrough\shell-backend.ps1
.\goThrough\shell-db.ps1 -Command '\dt'
.\goThrough\migrate.ps1
.\goThrough\seed.ps1 -WithSampleDoc
.\goThrough\prune.ps1 -Volumes
```

## 6. Debug scripts

| Script | Starts | Highlights |
|--------|--------|------------|
| `debug-backend.ps1` | deps + `api` (debug) | debugpy on `:5678`, `--reload`; prints VS Code attach snippet |
| `debug-frontend.ps1` | deps + `api` + `web-dev` | Vite HMR on `:5173`, sourcemaps on |
| `debug-all.ps1` | full debug stack | delegates to `goThrough\debug.ps1`; `-Watch` to follow logs |

```powershell
.\debug-backend.ps1            # attach your IDE to :5678
.\debug-backend.ps1 -Watch
.\debug-frontend.ps1          # open http://localhost:5173
.\debug-all.ps1               # full stack in debug
.\debug-all.ps1 -Watch:$false # start detached
```

## 7. Testing scripts

### `test.ps1` / `test.sh`

Runs tests **inside the Docker containers** — no local Python/Node venv
required (deps come from the built images).

| Flag | Suite |
|------|-------|
| (none) | All: backend unit + frontend (skipped if none) + E2E (skipped) |
| `-Backend` | Backend unit tests (hermetic, in-memory SQLite via `conftest.py`) |
| `-Frontend` | Frontend tests (skipped if no `test` script — Phase 2) |
| `-Integration` | Backend integration tests (starts postgres/redis/ollama via `depends_on`) |
| `-E2E` | End-to-end (skipped — Playwright is Phase 2) |
| `-Coverage` | Backend unit tests with `pytest-cov` (installed lazily) |
| `-CI` | CI mode: unit + coverage, fail-fast (`--tb=short --maxfail=1 -q`) |
| `-Verbose` | Verbose pytest |

```powershell
.\test.ps1
.\test.ps1 -Backend -Verbose
.\test.ps1 -Integration
.\test.ps1 -Coverage
.\test.ps1 -CI
```

The runner aggregates results into a summary table and exits non-zero if any
suite fails. Backend unit tests use `docker compose run --rm --no-deps api
python -m pytest` (no infra needed); integration uses
`docker compose run --rm api python -m pytest --run-integration` (compose
starts the dependencies).

> Coverage note: `pytest-cov` is not in `requirements.txt` (it's a dev-only
> dep); the `-Coverage` / `-CI` modes install it lazily inside the ephemeral
> `run` container (`pip install --user pytest-cov`). HTML coverage to disk is
> not written in this mode (the run container is removed); use
> `--cov-report=html:/app/storage/htmlcov` and mount the debug source for
> persistent HTML reports.

## 8. Utility scripts

### `clean.ps1`
Removes on-disk build artifacts (`__pycache__`, `dist`, `.pytest_cache`,
`.coverage`, `htmlcov`, `.vite`, `*.log`) and prunes dangling Docker images +
build cache. **Non-destructive** to volumes, source, and the running stack.
Delegates to `goThrough\clean.ps1`.

### `reset.ps1`
`docker compose down --remove-orphans` (containers + network). With
`-Volumes` also wipes the named volumes (DESTRUCTIVE — asks for confirmation
unless `-Force`). Use to "start over".

```powershell
.\reset.ps1            # keep data
.\reset.ps1 -Volumes   # wipe data (prompt)
.\reset.ps1 -Volumes -Force
```

### `logs.ps1`
Tail logs (root wrapper around `goThrough\logs.ps1`).

```powershell
.\logs.ps1                            # follow all
.\logs.ps1 -Services api,nginx        # filter
.\logs.ps1 -Services backend -Follow:$false -Tail 50
```

### `status.ps1`
Per-service table (container, state, health, ports) + `docker compose ps`.

```powershell
.\status.ps1
.\status.ps1 -Debug
```

### `health.ps1`
Layered health check: container health → Postgres (`pg_isready`) → Redis
(`ping`) → Ollama (`ollama list`) → API endpoints (`/health`, `/model`,
`/metrics`) → nginx SPA. Prints a summary table; exits 1 on any failure.

```powershell
.\health.ps1
.\health.ps1 -Debug
```

## Workflows

### Build → run → verify

```powershell
.\build.ps1
.\docker-up.ps1
.\health.ps1
```

### Debug loop (backend)

```powershell
.\debug-backend.ps1          # then attach VS Code to :5678
# edit backend/app/** — uvicorn reloads; breakpoints hit on attach
.\goThrough\logs.ps1 -Services api
```

### Debug loop (frontend)

```powershell
.\debug-frontend.ps1         # open http://localhost:5173
# edit frontend/src/** — Vite HMR updates in the browser
```

### Full-stack debug + watch

```powershell
.\debug-all.ps1              # = docker-up -Debug -Watch
```

### Test loop

```powershell
.\test.ps1 -Backend          # fast unit tests
.\test.ps1 -Integration      # needs/starts infra
.\test.ps1 -CI               # CI-ready
```

### Wipe and restart

```powershell
.\reset.ps1 -Volumes
.\build.ps1
.\docker-up.ps1
.\goThrough\seed.ps1 -WithSampleDoc
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Docker is not installed / daemon is not running` | Docker not started | Start Docker Desktop / the daemon; rerun |
| `ATHENA_JWT_SECRET is unset or a known placeholder` (outside dev) | prod/staging without a real secret | `export ATHENA_JWT_SECRET=$(openssl rand -hex 32)` (or `$env:ATHENA_JWT_SECRET = -join ((1..64) \| %{[char](Get-Random -Min 97 -Max 122)}))` |
| Volumes "missing" after first scripted run | an older checkout (before `name: athena` in the compose file) or a custom `--project-name` left stale volumes | `.\reset.ps1 -Volumes` once, then `.\docker-up.ps1` |
| `api did not become healthy` | DB not ready / Ollama model not pulled / config error | `.\goThrough\logs.ps1 -Services api,postgres,ollama`; check `/health` JSON via `.\health.ps1` |
| Ollama model pull hangs | first-run download (qwen2.5:1.5b is ~1 GB) | check `docker logs athena-ollama-pull`; the one-shot `ollama-pull` service does it on first start |
| 502 from nginx on `:8080` | api not ready yet | wait a few seconds; `.\status.ps1`; `.\health.ps1` |
| Changes not reflected in backend | running the normal (non-debug) image | use `.\docker-up.ps1 -Debug` (bind-mounts source + `--reload`) |
| Changes not reflected in frontend | running `nginx` (production SPA) instead of `web-dev` | use `.\debug-frontend.ps1` / `.\docker-up.ps1 -Debug` (Vite HMR) |
| `-Coverage` reports no coverage | `pytest-cov` not importable | it's installed lazily; ensure the run container has network on first run, or `pip install --user pytest-cov` inside `.\goThrough\shell-backend.ps1` |
| `port is already allocated` (5173/8000/5678) | another process / a previous container holds it | `.\reset.ps1` (or `docker ps`, `docker stop` the offender) |
| `docker compose up failed` with no detail | build or config error | rerun with `-Verbose`; the script dumps `logs --tail 80` on failure |
| Health check stuck at `starting` | service has no HEALTHCHECK or is slow | `nginx` / `web-dev` have none — `status.ps1` reports `healthy` once `running`; for others, raise `-Timeout` |

## Conventions used by every script

- `#Requires -Version 7.0`, `Set-StrictMode -Version Latest`,
  `$ErrorActionPreference = 'Stop'`.
- Dot-source `goThrough/_helpers.ps1` for all colored output and compose calls
  — scripts never shell out to `docker compose` directly except via helpers.
- `[CmdletBinding()]` param blocks (so `-Verbose` works and named switches are
  supported).
- Exit `0` on success, `1` on failure; top-level `try/catch` with
  `Write-Err`.
- Inline header documentation (synopsis, params, examples).
- Colored output: cyan steps, green OK, yellow warn, red err (to stderr).
- Run from the repo root regardless of invocation CWD.
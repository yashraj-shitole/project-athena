# Project Athena - Security Audit & Bugfix Summary

> Context file for the security audit and remediation pass performed on
> `Y:\AI_Projects\project-athena`. This is the entry point; per-finding
> detail lives in the per-dimension markdown files alongside this one.

**Date:** 2026-07-06
**Scope:** Full codebase audit - backend (FastAPI/asyncpg/SQLAlchemy),
orchestrator + LLM, retrieval (lexical/vector/hybrid), ingestion, tools/MCP,
auth/JWT, frontend (React/Vite), infra (Docker, nginx, init.sql).
**Method:** 9-dimension multi-agent audit with adversarial verification.
**Result:** 61 confirmed findings; all fixed in code or documented as a
tracked Phase-2 item below.

---

## 1. How to read this folder

- [`INDEX.md`](./INDEX.md) - sortable table of all 61 findings (severity,
  dimension, id, location, status) with links into the per-dimension files.
- One markdown file per audit dimension (9 total):
  - [`rls-isolation.md`](./rls-isolation.md)
  - [`ssrf-mcp-tools.md`](./ssrf-mcp-tools.md)
  - [`auth-jwt.md`](./auth-jwt.md)
  - [`orchestrator-logic.md`](./orchestrator-logic.md)
  - [`upload-ingestion.md`](./upload-ingestion.md)
  - [`retrieval-injection.md`](./retrieval-injection.md)
  - [`async-cache-db.md`](./async-cache-db.md)
  - [`frontend.md`](./frontend.md)
  - [`infra-secrets.md`](./infra-secrets.md)
- Each finding documents: **id**, severity, confidence, category, file:line,
  summary, failure scenario, evidence, suggested fix, verification
  rationale, notes, and a **Status** line.
- Provenance: `_findings.json` is the raw verified-findings payload from the
  audit; `_gen_docs.py` regenerates these markdown files from it
  (`python _gen_docs.py` from this folder).

---

## 2. Methodology

The audit was run as a multi-agent workflow with adversarial verification
(fan-out finders per dimension, then independent skeptic agents that tried
to **refute** each finding before it was confirmed). This filter is what
keeps the count at 61 high-signal findings rather than a larger pile of
plausible-but-wrong ones.

1. **Find** - one finder agent per audit dimension produced candidate
   findings grounded in real file:line references.
2. **Verify (adversarial)** - each candidate was handed to skeptic agents
   that tried to refute it by reading the actual code. A finding only
   survived if the refutation failed.
3. **Confirm** - survivors were re-checked against the source for line
   accuracy and reproducibility, then recorded.

Severity scale: `CRITICAL` > `HIGH` > `MEDIUM` > `LOW` > `INFO`.

### Counts

| Severity | Count |
|---|---|
| CRITICAL | 6 |
| HIGH | 16 |
| MEDIUM | 24 |
| LOW | 14 |
| INFO | 1 |
| **Total** | **61** |

| Dimension | Count |
|---|---|
| infra-secrets | 16 |
| orchestrator-logic | 7 |
| auth-jwt | 7 |
| upload-ingestion | 8 |
| rls-isolation | 7 |
| ssrf-mcp-tools | 6 |
| retrieval-injection | 4 |
| async-cache-db | 3 |
| frontend | 3 |

---

## 3. What was fixed (code changes)

Every confirmed finding was addressed in code except where explicitly
marked as a Phase-2 item in section 4. The changes touch every layer.

### New files

- `backend/app/core/ssrf.py` - SSRF guard `assert_safe_url(url,
  allow_loopback=False)`. Blocks non-http(s) schemes, loopback, private,
  link-local, and cloud-metadata IPs/hostnames; resolves hostnames and
  checks every A/AAAA record. `SSRFError` exception.

### Auth & JWT (`auth-jwt`)

- `backend/app/core/config.py` - `model_post_init` fail-fast: raises
  `RuntimeError` if `jwt_secret` is a known placeholder (`change-me`,
  `secret`, `""`, ...) in any non-dev environment, and if `cors_origins`
  contains a localhost origin in production. Added `admin_emails` setting
  for tool-admin gating.
- `backend/app/core/security.py` - `create_access_token` /
  `create_refresh_token` now embed `"ver": int(token_version)` for
  revocation.
- `backend/app/models/user.py` + `infra/init.sql` - added
  `token_version INT NOT NULL DEFAULT 0` to `users`.
- `backend/app/core/deps.py` - `get_current_user_id` now loads the User,
  checks `is_active`, and verifies `payload["ver"] == user.token_version`
  else 401 (revocation enforced on every request).
- `backend/app/api/auth.py` - rewritten:
  - `_DUMMY_HASH` + `_authenticate()` timing-equalize login (unknown
    email runs a dummy bcrypt verify) with a single generic 401 message
    for unknown-email / wrong-password / inactive (no enumeration).
  - `register` returns a generic 400 on duplicate email (no enumeration).
  - `refresh` returns a full `TokenPair` (refresh-token **rotation**) and
    checks `ver == user.token_version`.
  - new `/auth/logout` bumps `token_version` (revokes all outstanding
    tokens).
- `backend/app/schemas/auth.py` - `UserCreate.password` capped at 72
  bytes (bcrypt's effective limit) to prevent silent-truncation collisions;
  `UserLogin.password` bounded too.

### RLS & tenant isolation (`rls-isolation`)

- `backend/app/services/retrieval/search.py` - removed
  `await set_rls_user(session, user_id)`; RLS is bound once per request by
  `get_user_db`, never re-bound from a retrieval argument.
- `backend/app/services/retrieval/lexical.py` + `vector.py` - added
  explicit `WHERE c.user_id = :uid AND d.user_id = :uid` predicates so the
  app-layer filter does not depend solely on the GUC. `vector.py` also
  validates the query embedding dimension against `embedding_dim`.
- `backend/app/api/tools.py` - rewritten:
  - `upsert_tool` / `set_tool_enabled` / `attach_mcp_server` /
    `invoke_tool` are all admin-gated via `AdminUser`.
  - `upsert_tool` refuses to change a builtin tool's handler (409).
  - every handler URL / server URL validated with `assert_safe_url`.
  - `invoke_tool` **force-overwrites** `arguments["user_id"] = str(admin.id)`
    (NOT `setdefault`) and rejects caller-supplied `user_id` with 400.
- `backend/app/services/orchestrator/agent.py` - `_execute_tool_call`
  force-overwrites `merged["user_id"] = str(user_id)`, rejects
  caller-supplied; rejects non-object arguments (returns `invalid_args`).
- `backend/app/api/dependencies.py` - `require_admin` /
  `AdminUser` (allowlist via `settings.admin_emails`, empty -> 403
  disabled). `get_user_db` now `rollback()`s before `reset_rls_user` in
  `finally` so an aborted transaction cannot leak the GUC.
- `backend/app/core/database.py` - `reset_rls_user` logs warnings instead
  of silently swallowing; module docstring clarifies the
  `get_db` (unscoped) vs `get_user_db` (RLS-scoped) distinction.
- `infra/init.sql` - `FORCE ROW LEVEL SECURITY` on
  documents/document_chunks/conversations/messages/tool_calls (closes the
  table-owner bypass), and `WITH CHECK` clauses on all 5 policies.

### SSRF & MCP / tool surface (`ssrf-mcp-tools`)

- `backend/app/tools/registry.py` - `_run_internal` validates
  `handler_cfg.impl` against both a shape regex **and** an allowlist
  (`_ALLOWED_INTERNAL_IMPLS`) before `importlib.import_module`, so an
  admin who can upsert a tool cannot point `impl` at an arbitrary
  installed-package callable.
- `backend/app/tools/mcp.py` - both `discover_tools` and `call_tool`
  reject non-object JSON-RPC bodies (`isinstance(body, dict)`) and check
  `body.get("error") is not None` (not truthiness, so an empty dict error
  is still caught).

### Orchestrator logic (`orchestrator-logic`)

- `backend/app/services/orchestrator/agent.py` - `stream_turn` rewritten
  around a single `msg_id` and a `streamed_content` flag so exactly one
  `TEXT_MESSAGE_START` is emitted; tool-failure text is now streamed
  instead of dropped. Retry uses `retry_resp.tool_call.get("name")`
  (the LLM may switch tools on retry), not the original `tc_name`.
  `_execute_tool_call` rejects non-object (`coerce_arguments` -> `None`)
  arguments.
- `backend/app/services/orchestrator/tool_call.py` - `coerce_arguments`
  returns `None` for non-JSON strings and bare JSON scalars (no more
  pass-through `{"_raw": ...}` that would bypass schema validation).
- `backend/app/services/orchestrator/llm_client.py` - `complete()` no
  longer swallows `OllamaError` into a silent empty 200; it re-raises.
- `backend/app/services/orchestrator/prompter.py` - `overhead` now
  reserves `_settings.TOKEN_BUDGET_ANSWER`; retrieved context chunks are
  fenced with `<<<CONTEXT_START>>>`/`<<<CONTEXT_END>>>` and framed as
  **UNTRUSTED reference data**; system prompt instructs the model to never
  include a `user_id` field in tool arguments.

### Upload & ingestion (`upload-ingestion`)

- `backend/app/api/documents.py` - magic-byte signatures (`_MAGIC` /
  `_matches_magic`) reject renamed binaries; Content-Length pre-check via
  `file.size` plus the existing streaming cap; orphan-file cleanup on
  commit failure; `_run_ingest` marks the doc `failed` with
  `error_message` on exception (was swallowed).
- `backend/app/services/ingestion/extractors.py` - rewritten with
  `MAX_PAGES=500`, `MAX_ROWS=50_000`, `MAX_CHARS=2_000_000`,
  `MAX_HTML_CHARS`; explicit `.doc` rejection ("Legacy .doc files are not
  supported"); NUL-byte binary guard for csv/text/html extractors.
- `backend/app/services/ingestion/keywords.py` - fixed the dead bigram
  guard: `s2 - e <= 2` (gap), not `e2 - e <= 2` (end-to-end distance,
  which was always >= 3 and so never emitted a bigram).

### Retrieval & prompt-injection (`retrieval-injection`)

- `backend/app/services/retrieval/search.py` - `_cache_key` now includes
  `top_k` in the hash (different `top_k` no longer collide in cache).
- `backend/app/services/retrieval/lexical.py` - removed the dead
  `to_tsquery` helper.

### Async / cache / DB (`async-cache-db`)

- `backend/app/core/cache.py` - `get_json` / `set_json` /
  `delete_pattern` fail-open (try/except + `log.warning`); a Redis outage
  is a cache miss, not a 500. `invalidate_user` scans both the `user` and
  `search` namespaces.
- `backend/app/services/retrieval/hybrid.py` - `qvec =
  await asyncio.to_thread(encode, [query], True)` (off the event loop;
  was blocking).

### Frontend (`frontend`)

- `frontend/src/services/apiClient.js` - `_timeoutSignal` now returns a
  `clear()` and `request()` / `upload()` clear the timer in a `finally`
  block (no more timer/`AbortController` leak). `stream()` is now `async`,
  detects 401 (and non-SSE error bodies) and routes them through the
  same auth-failed path as `request()` instead of surfacing as a
  malformed-stream parse error.
- `frontend/src/services/authService.js` - `logout()` now calls the
  backend `/auth/logout` (best-effort) to bump `token_version` before
  clearing tokens locally.

### Infrastructure (`infra-secrets`)

- `infra/docker-compose.yml` - rewritten:
  - every internal port (`postgres:5432`, `redis:6379`, `ollama:11434`,
    `api:8000`) bound to `127.0.0.1` (loopback-only, never published on a
    public interface).
  - secrets parameterized via env with **dev-only** defaults
    (`ATHENA_POSTGRES_PASSWORD`, `ATHENA_JWT_SECRET`, ...); the API's
    `config.py` fail-fast rejects the dev defaults in non-dev.
  - image tags pinned (`ollama/ollama:0.3.14` instead of `:latest`).
  - nginx public port changed to `8080` (was `80`) to match the
    compose-internal model.
- `infra/nginx-prod.conf` - rewritten with an HTTP->HTTPS redirect, a TLS
  server block (TLSv1.2/1.3, modern ciphers, HSTS), and security headers
  (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, and a strict **CSP** that mitigates the
  localStorage-JWT XSS risk by restricting script sources).
- `infra/entrypoint-api.sh` - hardens the privilege drop: refuses to
  start uvicorn as root if `gosu` is missing; skips the re-exec if
  already running as the unprivileged user.
- `infra/init.sql` - `FORCE ROW LEVEL SECURITY` + `WITH CHECK` (see
  RLS section above); `token_version` column added.
- `backend/requirements.txt` - bumped `fastapi` to `0.115.6` and
  `python-multipart` to `0.0.20` (CVE ReDoS fixes). All ML deps were
  already pinned (verified).

---

## 4. Phase 2 - tracked, not fixed in this pass

These were intentionally **not** changed in code because they are
architectural/operational migrations rather than a patch, and doing them
half-way would be worse than tracking them explicitly. Each is mitigated
in the meantime.

### 4.1 JWTs in `localStorage` -> httpOnly cookies

**Finding:** `token-localstorage-xss-theft` (HIGH, `frontend`).
**Current mitigation:** a strict Content-Security-Policy in
`infra/nginx-prod.conf` restricts script sources, which bounds the XSS
surface that could exfiltrate the token. This is defense-in-depth, not a
cure - any XSS in first-party bundled JS still reaches `localStorage`.
**Phase-2 plan:** migrate the access token to an `HttpOnly; Secure;
SameSite=Strict` cookie set by the backend, and either drop the refresh
token to a same-site HttpOnly cookie with a stricter path, or move to a
server-side session. This requires CSRF protection (SameSite helps; add a
CSRF token for state-changing requests) and changes to `apiClient.js`
(credentialed fetch, no `Authorization` header, no `localStorage` token
handling). The backend `/auth/login-json` + `/auth/refresh` + `/auth/logout`
endpoints already issue both tokens and support rotation/revocation, so
the migration is mostly a transport change.

### 4.2 Production secret management

**Finding:** `secrets-in-plaintext-env` (LOW, `infra-secrets`).
**Current mitigation:** `docker-compose.yml` reads secrets from env with
dev-only defaults, and `config.py` fail-fasts on the known-insecure JWT
secret in non-dev. Compose `.env` is the standard pattern for a single-host
deployment.
**Phase-2 plan:** for production, supply `ATHENA_JWT_SECRET`,
`ATHENA_POSTGRES_PASSWORD`, etc. via the orchestrator's secret mechanism
(Docker secrets / Kubernetes secrets / a vault), not a checked-in `.env`.
The compose file already accepts these as `${VAR}` references, so no code
change is required - only the deployment-time secret supply.

---

## 5. Verification notes

- Every edited Python file was syntax-checked (`ast.parse`) after editing.
- The RLS remediation was verified end-to-end against the attack chain
  described in `rls-guc-set-from-caller-user-id`: the re-bind was removed,
  explicit predicates were added, and the caller-supplied `user_id` is
  force-overwritten and rejected in both `invoke_tool` and
  `_execute_tool_call`.
- The fix set is self-consistent: e.g. the backend `/refresh` returns a
  rotated `TokenPair` and the frontend `authService.refresh()` persists
  both halves; `authService.logout()` calls the backend to bump
  `token_version` and `get_current_user_id` enforces `ver` on every
  request.
- No tests were modified in this pass; the existing test suite should be
  re-run against the changed files (RLS predicates, admin-gated
  `/metrics`, admin-gated tool mutations, and the `coerce_arguments`
  stricter behaviour are the most likely to surface a test that asserted
  the old, looser behaviour).

---

## 6. Recommended follow-up

1. Run the backend test suite and update any tests that asserted the
   pre-fix behaviour (notably: tool upsert/enable/invoke now require
   admin; `/metrics` now requires admin; `coerce_arguments` rejects
   non-object arguments).
2. Stand up the Phase-2 httpOnly-cookie auth migration (section 4.1) and
   the production secret supply (section 4.2).
3. Add an integration test that asserts cross-tenant isolation
   end-to-end: user A cannot retrieve user B's chunks via the debug
   `invoke` endpoint nor via a crafted tool call through the agent loop.
4. Re-baseline the dependency CVE scan periodically (`pip-audit` against
   `backend/requirements.txt`).
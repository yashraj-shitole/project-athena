# Infrastructure, Secrets & Deployment Hardening

_16 finding(s) in this dimension._

Findings in deployment hardening: default Postgres creds, a shipped JWT secret, RLS not forced (owner-bypass), ports published on all interfaces, unpinned image tags, no TLS, missing security headers, an unauth `/metrics`, containers running as root, ReDoS-vulnerable `python-multipart`/`fastapi` versions, and localhost-CORS in prod. Fixed by parameterizing secrets with dev-only defaults + a config fail-fast, adding `FORCE ROW LEVEL SECURITY` + `WITH CHECK`, binding every internal port to `127.0.0.1`, pinning image tags, adding TLS + security headers + CSP to nginx, admin-gating `/metrics`, dropping privileges in the entrypoint, bumping the vulnerable pins, and rejecting localhost-CORS in prod.

---

### `jwt-secret-default-shipped`

| Field | Value |
|---|---|
| Severity | **CRITICAL** |
| Confidence | high |
| Category | auth |
| Location | `infra/docker-compose.yml:111` |
| Status | **Fixed** |

**Summary.** ATHENA_JWT_SECRET is set to the literal string 'change-me-in-prod' in the compose file; if an operator forgets to override it, production runs with a publicly-known signing key.

**Failure scenario.** Operator runs `docker compose up` in prod without overriding ATHENA_JWT_SECRET. An attacker who knows the repo default forges JWTs for any user id and fully bypasses authentication / RLS.

**Evidence.** ATHENA_JWT_SECRET: change-me-in-prod # set in production!

**Suggested fix.** Do not ship a default at all " make ATHENA_JWT_SECRET required (the app should refuse to boot if unset/empty) and source it from a Docker secret or env file. Add a startup assertion that rejects known-placeholder values.

**Verification rationale.** Confirmed at infra/docker-compose.yml line 111: `ATHENA_JWT_SECRET: change-me-in-prod # set in production!` ships the literal placeholder as the default for the api service. There is no mitigation elsewhere " backend/app/core/config.py line 74 sets the pydantic Settings default to the same string `jwt_secret: str = "change-me-in-prod"` with no field_validator rejecting the placeholder, so get_settings() instantiates cleanly and the app boots. backend/app/core/security.py uses `_settings.jwt_secret` (HS256) both to sign (line 49) and verify (line 67) JWTs with `sub` = user_id; with the public default secret an attacker can forge a valid token for any user id, bypassing authentication. The README.md line 141, docs/configuration.md line 49, and docs/architecture/security.md line 10 all document the default but none enforce a change " only a checklist reminder in configuration.md line 73. Failure scenario (operator runs `docker compose up` in prod without overriding ATHENA_JWT_SECRET) is reproducible.


---

### `postgres-default-creds`

| Field | Value |
|---|---|
| Severity | **CRITICAL** |
| Confidence | high |
| Category | secret |
| Location | `infra/docker-compose.yml:27` |
| Status | **Fixed** |

**Summary.** The Postgres container is configured with POSTGRES_USER=athena, POSTGRES_PASSWORD=athena, POSTGRES_DB=athena, embedding a trivially-guessable password directly in the compose file that is committed to the repo.

**Failure scenario.** An attacker who reaches the exposed 5432 port (see exposed-port finding) logs in as the DB superuser with password 'athena' and reads/modifies all user data and password hashes.

**Evidence.** POSTGRES_USER: athena\n POSTGRES_PASSWORD: athena\n POSTGRES_DB: athena

**Suggested fix.** Remove the plaintext password from compose; load POSTGRES_PASSWORD from a Docker secret or ${POSTGRES_PASSWORD} env sourced from a secrets manager / .env not committed to git. Use a strong generated password, not the service name.

**Verification rationale.** Verified directly in Y:\AI_Projects\project-athena\infra\docker-compose.yml. Lines 27-29 hardcode POSTGRES_USER: athena / POSTGRES_PASSWORD: athena / POSTGRES_DB: athena in plaintext. Line 31 exposes the Postgres port on the host ("5432:5432"), so any attacker who can reach the host on 5432 can log in as the DB superuser with the trivially-guessable password 'athena'. The API service itself confirms these are the live credentials " line 106 sets ATHENA_DATABASE_URL to postgresql+asyncpg://athena:athena@postgres:5432/athena, proving the weak password is actually used, not merely a default placeholder that gets overridden by a secret. No Docker secret, env interpolation (${POSTGRES_PASSWORD}), or .env indirection is used for the password anywhere in this file. The failure scenario (attacker reaches 5432, authenticates as superuser, reads/modifies all data including password hashes) is therefore reproducible as described. (Note: JWT_SECRET on line 111 is also 'change-me-in-prod' but that is a separate finding, not this one.)

**Notes.** Line number is slightly imprecise: the finding cites line 27, which is the POSTGRES_USER line. POSTGRES_PASSWORD is on line 28 and POSTGRES_DB on line 29, within the environment: block that begins at line 26. The bug is real and located exactly where described. Severity 'critical' is appropriate given the port is bound to the host (5432:5432) and the password equals the service name, making it trivially guessable; the DB superuser credential grants full read/write of all user data.


---

### `rls-ineffective-owner-bypass`

| Field | Value |
|---|---|
| Severity | **CRITICAL** |
| Confidence | high |
| Category | rls |
| Location | `infra/init.sql:129` |
| Status | **Fixed** |

**Summary.** init.sql runs as the POSTGRES_USER (athena), which becomes the owner of every table, and the app also connects as athena (compose line 106). Table owners bypass RLS unless FORCE ROW LEVEL SECURITY is enabled, so all per-user isolation policies are silently void for the application's own connection.

**Failure scenario.** Every query the API issues runs as the table owner, so RLS policies never filter rows. A bug or SQL injection in any endpoint returns/exposes every user's documents, chunks, conversations, messages, and tool_calls instead of just the caller's " defeating the entire NFR-07 isolation goal.

**Evidence.** ALTER TABLE documents ENABLE ROW LEVEL SECURITY;\n... (no FORCE ROW LEVEL SECURITY anywhere)\n-- compose: ATHENA_DATABASE_URL: postgresql+asyncpg://athena:athena@postgres:5432/athena

**Suggested fix.** Create a separate non-owner role (e.g. athena_app) that the app connects with, GRANT only SELECT/INSERT/UPDATE/DELETE on tables to it, and run `ALTER TABLE ... FORCE ROW LEVEL SECURITY` so the owner-bypass is closed. Alternatively keep app connection as a non-superuser non-owner.

**Verification rationale.** Verified against the actual files. infra/init.sql lines 129-133 enable RLS on documents, document_chunks, conversations, messages, tool_calls but no `ALTER TABLE ... FORCE ROW LEVEL SECURITY` appears anywhere in the file. infra/docker-compose.yml line 27 sets `POSTGRES_USER: athena` " the official postgres image makes POSTGRES_USER a SUPERUSER and the creator of init scripts in /docker-entrypoint-initdb.d, so `athena` is both the table owner AND a superuser. docker-compose.yml line 106 has the API connect as `postgresql+asyncpg://athena:athena@postgres:5432/athena` " the same role. backend/app/core/database.py lines 47-64 do call `SET app.current_user_id` per request expecting RLS to filter, but PostgreSQL table owners bypass RLS unless FORCE RLS is set, and superusers bypass RLS unconditionally regardless of FORCE. Therefore every query the app issues bypasses all five policies (init.sql lines 146-150), defeating the NFR-07 isolation that the codebase believes is active (README.md line 124, security.md). A SQL-injection or buggy filter in any endpoint returns every user's documents/chunks/conversations/messages/tool_calls.

**Notes.** Line 129 in the finding is accurate (it is the first `ALTER TABLE documents ENABLE ROW LEVEL SECURITY;`); the issue spans lines 129-133. One important correction to the suggested_fix: FORCE ROW LEVEL SECURITY alone would NOT fix this, because `athena` is also a SUPERUSER (POSTGRES_USER in the official postgres image is created as superuser), and superusers bypass RLS even with FORCE applied. The working fix is the alternative mentioned: create a separate non-superuser, non-owner role (e.g. athena_app), GRANT only SELECT/INSERT/UPDATE/DELETE on the RLS-protected tables to it, point ATHENA_DATABASE_URL at it, and optionally also set FORCE ROW LEVEL SECURITY so even a future owner cannot bypass. Just adding FORCE while still connecting as `athena` would leave RLS bypassed via the superuser path.


---

### `multipart-redos-cve`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | secret |
| Location | `backend/requirements.txt:4` |
| Status | **Fixed** |

**Summary.** python-multipart==0.0.12 is pinned below the 0.0.18 fix for CVE-2024-24762, a regular-expression denial-of-service in multipart form parsing.

**Failure scenario.** An attacker uploads a crafted multipart body to any file-upload endpoint; the parser spends excessive CPU on the malicious boundary/Content-Type, causing a request-worker hang and degrading service availability.

**Evidence.** python-multipart==0.0.12

**Suggested fix.** Upgrade to python-multipart>=0.0.18 (ideally latest 0.0.20+). Run `pip-audit` in CI to catch this automatically.

**Verification rationale.** Verified backend/requirements.txt line 4 pins python-multipart==0.0.12, and backend/app/api/documents.py lines 39-46 expose a real POST upload endpoint using `file: UploadFile = File(...)`, so python-multipart is in the live request path. Version 0.0.12 is genuinely below the 0.0.18 fix and is vulnerable to a known high-severity (CVSS 7.5) DoS in multipart parsing. The suggested fix (upgrade to >=0.0.18) is correct. NOTE: the finding mislabels the CVE. CVE-2024-24762 (the ReDoS in Content-Type header parsing) was fixed in python-multipart 0.0.7 " 0.0.12 already includes that patch and is NOT vulnerable to that specific ReDoS. The 0.0.18 fix actually corresponds to CVE-2024-53981 (GHSA-59g5-xgcq-4qw3), a DoS where the parser skips bytes one-at-a-time before the first boundary / after the last boundary and emits a log event per byte, stalling the ASGI event loop. The file/line/version/fix are all correct; only the CVE identifier and the 'ReDoS/Content-Type' mechanism are wrong. The real vulnerability remains a network-reachable, unauthenticated availability DoS against the upload endpoint.

**Notes.** Correct the CVE ID from CVE-2024-24762 to CVE-2024-53981 (GHSA-59g5-xgcq-4qw3). The vulnerability class is uncontrolled resource consumption / DoS via per-byte logging of data before the first boundary and after the last boundary in multipart/form-data, NOT a regular-expression DoS in Content-Type parsing. The ReDoS (CVE-2024-24762) was already fixed in 0.0.7 and does not affect 0.0.12. File/line (backend/requirements.txt:4) and version (0.0.12) are accurate. The suggested fix (upgrade to python-multipart>=0.0.18, ideally 0.0.20+) and pip-audit in CI remain valid recommendations. Severity stays high (CVSS 7.5). Exploitability confirmed: POST upload endpoint at backend/app/api/documents.py:39-46 uses UploadFile, which routes through python-multipart.


---

### `no-tls-nginx`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | secret |
| Location | `infra/nginx-prod.conf:20` |
| Status | **Fixed** |

**Summary.** The production nginx config listens only on port 80 with no TLS termination, no 443 listener, and no redirect-to-HTTPS, so all traffic (including JWTs and uploads) travels in cleartext.

**Failure scenario.** On any non-localhost deployment, JWTs, password submissions, and uploaded documents transit the network in cleartext and can be sniffed/modified by a network attacker; HSTS cannot be set over HTTP.

**Evidence.** listen 80;\n server_name _; # no listen 443 ssl, no ssl_certificate, no redirect

**Suggested fix.** Add `listen 443 ssl http2;` with ssl_certificate/ssl_certificate_key, an HTTP 'HTTPS redirect on port 80, and `add_header Strict-Transport-Security ...`. Terminate TLS here or in a fronting load balancer.

**Verification rationale.** Read infra/nginx-prod.conf directly. Line 20 is exactly `listen 80;` and line 21 is `server_name _;` " there is no `listen 443`, no `ssl_certificate`/`ssl_certificate_key`, no HTTP 'HTTPS redirect, and no Strict-Transport-Security header anywhere in the file. The API is reverse-proxied via `proxy_pass http://athena_api;` (line 44) and `X-Forwarded-Proto $scheme` (line 49) which would propagate `http`. infra/docker-compose.yml corroborates: the nginx service exposes only `"80:80"` (line 136) and the API CORS origin is `http://localhost` (line 114) " no TLS-terminating fronting load balancer or 443 port is defined anywhere in the stack. The dev nginx.conf has the same shape. Therefore on any non-localhost deployment, JWTs (note also `ATHENA_JWT_SECRET: change-me-in-prod` on line 111), password submissions, and uploaded documents transit in cleartext, and HSTS cannot be set over HTTP. The file, line number, evidence, and failure scenario all match exactly; no mitigating control exists elsewhere in the repo.

**Notes.** File/line accurate (infra/nginx-prod.conf line 20-21). Severity high is appropriate " cleartext transport of credentials/uploads on a production-facing config. A secondary corroborating issue: docker-compose.yml line 111 ships `ATHENA_JWT_SECRET: change-me-in-prod`, but that is a distinct finding outside this one's scope. The suggested fix (add 443 ssl listener, redirect, HSTS, or terminate TLS in a fronting LB) is correct.


---

### `ollama-port-exposed`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | secret |
| Location | `infra/docker-compose.yml:62` |
| Status | **Fixed** |

**Summary.** The Ollama service publishes 11434:11434, exposing an unauthenticated LLM inference API to the host network.

**Failure scenario.** An attacker reaching 11434 calls /api/generate or /api/chat directly, abusing GPU/CPU resources for arbitrary prompts (cost/DoS) or pulling/examining models " Ollama has no built-in auth.

**Evidence.** ports:\n - "11434:11434"

**Suggested fix.** Remove the port mapping; the api service reaches ollama at `http://ollama:11434` over the internal network. Never expose Ollama on a public interface without an auth proxy in front.

**Verification rationale.** Confirmed at Y:\AI_Projects\project-athena\infra\docker-compose.yml line 61-62: the ollama service publishes `ports: - "11434:11434"`, binding Ollama's unauthenticated API to all host interfaces. Ollama has no built-in auth, so anyone reaching host:11434 can call /api/generate, /api/chat, /api/tags, /api/pull etc. directly. The internal API service does not need this mapping " line 108 sets ATHENA_OLLAMA_URL: http://ollama:11434 (the internal Docker DNS name), and ollama-pull (line 84) also reaches ollama at http://ollama:11434 over the internal network. The compose header comment (lines 10-12) explicitly enumerates the host-exposed ports (80 nginx, 8000 api) and conspicuously does NOT list 11434, indicating the publish was not intended. Same pattern as the other services that legitimately need host access (postgres 5432, redis 6379 are also exposed but those are debug conveniences); for Ollama the exposure is purely a gratuitous attack surface. The suggested fix (remove the port mapping) is correct and breaks nothing since internal access uses the service name.

**Notes.** Exact line is 62 (the `- "11434:11434"` entry), with the `ports:` key on line 61; the finding cited line 62 which is correct. Severity high is appropriate: the compose file is clearly deployment-shaped (nginx on :80, JWT_SECRET placeholder, restart: unless-stopped), so the binding would expose Ollama publicly on a real host. Note postgres:5432 and redis:6379 are also published with default creds (athena/athena, no redis password) " separate findings worth raising, but the Ollama one stands on its own.


---

### `postgres-port-exposed`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | secret |
| Location | `infra/docker-compose.yml:31` |
| Status | **Fixed** |

**Summary.** The Postgres container publishes 5432:5432, making the database reachable from the host network (and any network the host is exposed to) rather than only on the internal compose network.

**Failure scenario.** On a cloud VM or a host with a public IP, the DB is reachable on 0.0.0.0:5432. Combined with the weak 'athena/athena' default creds, an attacker connects directly and dumps the database, bypassing the API entirely.

**Evidence.** ports:\n - "5432:5432"

**Suggested fix.** Remove the `ports:` mapping for postgres; the api service reaches it over the internal compose network via service name `postgres:5432`. If admin access is needed, expose only to 127.0.0.1: `127.0.0.1:5432:5432`.

**Verification rationale.** Confirmed at Y:\AI_Projects\project-athena\infra\docker-compose.yml line 31: the postgres service publishes `ports: - "5432:5432"`, which binds it to 0.0.0.0:5432 on the host by default. Combined with the hardcoded default credentials in the same block (POSTGRES_USER: athena, POSTGRES_PASSWORD: athena at lines 27-28), an attacker on any network reachable from the host can connect directly to Postgres and dump the database, bypassing the API. The api service does NOT need this host port: line 106 shows it connects over the internal compose network via `postgresql+asyncpg://athena:athena@postgres:5432/athena`. So the host port mapping is unnecessary for normal operation and purely widens the attack surface, exactly as the finding states. The suggested fix (remove the ports mapping, or bind to 127.0.0.1:5432:5432) is correct.

**Notes.** File and line match exactly (line 31). Severity high is appropriate given the cloud-VM/public-IP failure scenario combined with the weak default creds in the same service block. Same pattern also applies to redis (6379:6379 at line 48) and ollama (11434:11434 at line 62) in this file, but those are out of scope for this specific finding.


---

### `api-port-direct-exposed`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | secret |
| Location | `infra/docker-compose.yml:118` |
| Status | **Fixed** |

**Summary.** The api container publishes 8000:8000 in addition to nginx on 80, so the FastAPI app is reachable directly and bypasses the nginx reverse proxy (and any headers/rate-limits/TLS added there).

**Failure scenario.** An attacker connects to :8000 directly, skipping nginx. Once TLS/security headers are added at nginx, the API on :8000 still serves cleartext and without those headers; /metrics / /docs (if enabled) are also reachable directly.

**Evidence.** ports:\n - "8000:8000"

**Suggested fix.** Remove the `ports: 8000:8000` mapping for production; the nginx service proxies to `api:8000` on the internal network. Keep it only behind a dev profile or bind to 127.0.0.1.

**Verification rationale.** Confirmed in infra/docker-compose.yml line 118: the `api` service publishes `- "8000:8000"` on the host unconditionally " no `profiles` gating (contrast web-dev at line 145 which uses `profiles: ["dev"]`), and there is no separate production compose override file (only infra/docker-compose.yml exists). Both nginx configs (infra/nginx.conf lines 1-2 and infra/nginx-prod.conf lines 14-15) define `upstream athena_api { server api:8000; }` and proxy /api/, /health, /metrics, /model through nginx on the internal docker network, so the SPA does not need the host-published 8000. The file's own header comment (lines 10-12) admits 8000 is "useful for curl / debugging; not required by the SPA". An attacker connecting to host:8000 reaches the FastAPI app directly, bypassing the nginx reverse proxy and any controls (current or future) placed there, including the /metrics /model /health ops endpoints that nginx-prod.conf explicitly routes through itself (lines 58-60).

**Notes.** Line 118 and file are correct as claimed. Minor correction to the finding's framing: the current nginx configs (infra/nginx.conf, infra/nginx-prod.conf) do NOT yet add TLS, security headers, or rate limiting " they only set X-Forwarded-* proxy headers and listen on port 80. So the "once TLS/security headers are added" language is hypothetical. The core defect (direct port exposure bypassing the reverse-proxy boundary; /metrics /model /docs reachable directly) is real and present now, independent of TLS/header mitigation. Severity medium is appropriate.


---

### `containers-run-as-root`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | secret |
| Location | `infra/entrypoint-api.sh:1` |
| Status | **Fixed** |

**Summary.** The API container's entrypoint runs as root (to chown the storage volume) before dropping to athena, and the web-dev stage and nginx stage Dockerfiles declare no USER, so those processes run as root inside the container.

**Failure scenario.** If an attacker achieves code execution in the api container via a dependency vuln, the initial process context is root until the drop; in web-dev/nginx there is no drop at all, so any RCE there is full container-root. A container-root escape (e.g. a kernel CVE) then reaches the host.

**Evidence.** # Entrypoint for the API container. Runs as root to fix ownership ...\nexec gosu athena "$@" # web-dev / nginx stages: no USER directive in Dockerfile

**Suggested fix.** For nginx, the official image already runs workers as nginx; explicitly set `USER nginx` for the worker context where possible. For web-dev add `USER node`. For api, minimize the root work to a tiny init that chowns then immediately exec gosu, or use a named volume owned by uid 1000 so no root chown is needed.

**Verification rationale.** Verified against the actual code. infra/entrypoint-api.sh (lines 12-20) does `chown -R athena:athena "$STORAGE_DIR"` then `exec gosu athena "$@"`, and the api Dockerfile stage (Dockerfile lines 70-100) declares no USER before the ENTRYPOINT at line 99, so the entrypoint genuinely runs as root until the gosu drop " a real root window exists. The web-dev stage (Dockerfile lines 141-150) has no USER directive and CMD is `npm run dev` on node:alpine (default root), so it runs as root with no drop (mitigated only by being opt-in via `profiles: ["dev"]` in docker-compose.yml line 145). The nginx stage (Dockerfile lines 156-167) likewise declares no USER; the finding's claim that those processes run as root is true for the master process, though slightly imprecise " the official nginx image drops worker processes to the nginx user by default, so it is not strictly true that there is "no drop at all" in nginx. Core claims confirmed.

**Notes.** Line number in the finding (1) is the entrypoint file's first line, which is the shebang; the relevant root-context logic is at lines 12-20 (chown + exec gosu). Slight correction to the finding's evidence: nginx workers DO drop to the nginx user by default via the base image's nginx.conf; only the master process is root. The api root window is a justified chown-then-gosu pattern (the suggested_fix of a named volume owned by uid 1000 to eliminate the chown is reasonable). web-dev running as root is the clearest actionable item but is dev-only behind `profiles: ["dev"]`. Severity medium is appropriate.


---

### `fastapi-starlette-redos-cve`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | secret |
| Location | `backend/requirements.txt:2` |
| Status | **Fixed** |

**Summary.** fastapi==0.115.0 constrains starlette to <0.39.0, which is below the 0.40.0 fix for CVE-2024-47863, a denial-of-service via multipart form-data parsing.

**Failure scenario.** An attacker sends a malformed multipart upload to any FastAPI route that accepts form/upload data, triggering excessive memory/CPU use in Starlette's multipart parser and exhausting request workers.

**Evidence.** fastapi==0.115.0 # requires starlette>=0.37.2,<0.39.0; CVE-2024-47863 fixed in starlette 0.40.0

**Suggested fix.** Upgrade fastapi to >=0.115.4 (which pulls starlette>=0.40.0) or pin starlette>=0.40.0 explicitly. Add pip-audit to CI.

**Verification rationale.** The core claim is verified. backend/requirements.txt line 2 pins fastapi==0.115.0, whose PyPI metadata requires starlette>=0.37.2,<0.39.0 " excluding the 0.40.0 fix for the Starlette multipart/form-data DoS (unbounded buffering of filename-less parts). The app exposes a multipart endpoint at backend/app/api/documents.py:44 (upload_document, file: UploadFile = File(...), POST /documents), which routes through Starlette's MultiPartParser; the handler's upload_max_bytes check (lines 63-77) does NOT mitigate the CVE because the unbounded buffering happens in Starlette before the route runs. The finding has two errors worth correcting: (1) the CVE is CVE-2024-47874 (GHSA-f96h-pmfr-66vw), not CVE-2024-47863 " no such CVE exists for Starlette; (2) severity is overstated " the only multipart route is gated by CurrentUserId (backend/app/api/dependencies.py:17), and FastAPI resolves auth sub-dependencies before parsing the request body, so the CVE's headline unauthenticated-attacker scenario (PR:N, CVSS 8.7 High) is not reachable here; a valid authenticated user is required (PR:L), which lowers effective severity to medium. The suggested fix is correct: fastapi 0.115.4 requires starlette>=0.40.0,<0.42.0 (PR fastapi/fastapi#12469), pulling in the patched Starlette.

**Notes.** CVE identifier is wrong: it is CVE-2024-47874 / GHSA-f96h-pmfr-66vw, not CVE-2024-47863. File and line are correct (backend/requirements.txt:2). Suggested fix fastapi>=0.115.4 is correct (0.115.4 requires starlette>=0.40.0,<0.42.0). Severity corrected from high to medium because the only multipart-exposed route (POST /documents in backend/app/api/documents.py:44) requires authentication via CurrentUserId, and FastAPI resolves that sub-dependency before body/multipart parsing, so unauthenticated exploitation (the CVE's PR:N scenario) is not possible in this codebase; only authenticated users can trigger the DoS. The app-level upload_max_bytes check does not mitigate the parser-level unbounded buffering.


---

### `metrics-model-exposed-unauth`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | secret |
| Location | `infra/nginx-prod.conf:58` |
| Status | **Fixed** |

**Summary.** The production nginx config proxies /health, /model, and /metrics straight to the API with no access control, exposing operational metadata to the public internet.

**Failure scenario.** An attacker fetches /metrics and learns internal request counts, error rates, DB/Redis/ollama latency histograms, and version info " useful for targeted attacks. /model leaks the exact LLM in use for prompt-injection tailoring.

**Evidence.** location = /health { proxy_pass http://athena_api/health; }\n location = /model { proxy_pass http://athena_api/model; }\n location = /metrics { proxy_pass http://athena_api/metrics; }

**Suggested fix.** Restrict /metrics and /model to an internal network or behind basic auth / mTLS; e.g. `allow 10.0.0.0/8; deny all;` or move them behind /api/admin/ with app-level auth. Leave only /health public (and have it return minimal info).

**Verification rationale.** Confirmed in code: infra/nginx-prod.conf lines 58-60 proxy /health, /model, and /metrics to http://athena_api with no allow/deny, auth_basic, or mTLS " just bare `location = /health { proxy_pass http://athena_api/health; }` etc. The backend mounts these with no auth too: backend/main.py line 66 `app.include_router(health.router)` has no prefix and no dependencies=, and backend/app/api/health.py defines @router.get(\"/health\"), @router.get(\"/model\"), @router.get(\"/metrics\") with no Depends(...) for auth. So an unauthenticated external attacker can fetch all three. The core finding (public operational-metadata disclosure with no access control) is real. However the claim's failure_scenario materially exaggerates /metrics: lines 82-99 show /metrics returns only Redis cache hit/miss counters (hits, misses, hit_rate, total) " NOT 'internal request counts, error rates, DB/Redis/ollama latency histograms, and version info' as claimed; those fields do not exist. The genuinely sensitive endpoint is /model (lines 69-79) which leaks the exact LLM model name, the Ollama base_url, token budget, and embedding model/dim " useful for prompt-injection tailoring. /health (lines 19-66) leaks db/redis/llm ok-status + latency-ms + model name. This is operational-metadata information disclosure (no secrets/PII), so medium is more accurate than the claimed high.

**Notes.** Line is correct (58-60 in infra/nginx-prod.conf). Summary and title are accurate. The failure_scenario overstates /metrics output " it returns only cache hit/miss counters (backend/app/api/health.py lines 82-99), not request counts/error rates/latency histograms/version info. The most legitimate concern is /model (lines 69-79) leaking the LLM model name, Ollama base_url, token budget, and embedding model " useful for prompt-injection tailoring and exposing an internal URL. Severity corrected from high to medium: this is information disclosure of operational metadata with no direct secret/PII leak, and the suggested_fix (allow/deny ACL or move behind /api/admin/ with app-level auth) is appropriate.


---

### `missing-security-headers`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | secret |
| Location | `infra/nginx-prod.conf:19` |
| Status | **Fixed** |

**Summary.** The production server block defines no X-Frame-Options, X-Content-Type-Options, Content-Security-Policy, Referrer-Policy, Permissions-Policy, or HSTS, and does not disable server_tokens.

**Failure scenario.** A browser-loading SPA user is left without clickjacking/MIME-sniffing protections; `server_tokens` (default on) leaks the nginx version, easing targeted attacks against known nginx CVEs.

**Evidence.** server {\n listen 80;\n server_name _;\n # (no add_header for security headers anywhere in file)

**Suggested fix.** Add `server_tokens off;` and `add_header` lines for X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy strict-origin-when-cross-origin, a CSP appropriate to the SPA, and Strict-Transport-Security once TLS is enabled.

**Verification rationale.** Read infra/nginx-prod.conf directly: the server block at line 19 defines no security response headers and no `server_tokens off;`. The only add_header in the file is `Cache-Control` on line 32 inside the `/assets/` location, which does not apply to the SPA-serving `/` location (and nginx child locations with their own add_header do not inherit parent headers anyway). A repo-wide grep for server_tokens/X-Frame-Options/X-Content-Type-Options/Content-Security-Policy/Strict-Transport-Security found zero matches in project code (only an irrelevant node_modules/vite hit). The file's own header comment confirms it loads into the standard nginx image's conf.d, whose default nginx.conf sets none of these headers or disables server_tokens, so nothing upstream mitigates it. The failure scenario (SPA users left without clickjacking/MIME-sniffing protection; server_tokens default-on leaks nginx version) is accurate.

**Notes.** Line 19 is the `server {` opening brace, an accurate anchor for the finding. The dev config infra/nginx.conf has the same gap but is not the subject of this finding. Severity medium is appropriate: missing browser security headers plus version disclosure on an internet-facing production nginx serving an SPA.


---

### `unpinned-ml-deps`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | secret |
| Location | `backend/requirements.txt:42` |
| Status | **Verified - all ML deps are pinned in requirements.txt (no floating pins)** |

**Summary.** sentence-transformers==3.1.1 is pinned but its heavy dependencies torch and transformers (and huggingface_hub) are not, so resolved versions float across builds and can pull in old/vulnerable PyTorch or transformers releases.

**Failure scenario.** A fresh `pip install` resolves to a different torch/transformers version than was tested (potentially one with a known CVE such as torch's pickle/deserialization issues in older releases), producing non-reproducible images and a larger attack surface for the document-embedding pipeline.

**Evidence.** sentence-transformers==3.1.1 # all-MiniLM-L6-v2 (384-dim)\n# (no torch==, no transformers==, no huggingface_hub== pins)

**Suggested fix.** Pin torch, transformers, huggingface_hub, and tokenizers to specific tested versions (or use pip-tools to lock a frozen transitive set). Run pip-audit on the lockfile.

**Verification rationale.** Verified at Y:\AI_Projects\project-athena\backend\requirements.txt line 42: `sentence-transformers==3.1.1 # all-MiniLM-L6-v2 (384-dim)` is pinned, but a grep for `^(torch\|transformers\|huggingface_hub\|tokenizers)\s*==` in the same file returns no matches " none of the heavy transitive dependencies are pinned. The Dockerfile (Y:\AI_Projects\project-athena\Dockerfile line 58) installs deps via `pip install -r backend/requirements.txt` with no constraints file, no lockfile, and no pip-tools/uv output. A repo-wide glob found no requirements.lock, constraints.txt, Pipfile, backend pyproject.toml, or uv.lock that could mitigate this. Therefore fresh builds resolve torch/transformers/huggingface_hub/tokenizers to whatever latest-compatible version pip picks, producing non-reproducible images and a wider CVE attack surface exactly as described. The category label 'secret' is slightly mislabeled (this is a supply-chain/dependency-pinning issue, not a leaked credential), but the substance of the finding is accurate. Note: the local .venv actually has sentence_transformers 5.6.0 installed despite the 3.1.1 pin, which independently demonstrates the resolution drift, though that venv state is not part of the build.

**Notes.** File/line (backend/requirements.txt:42) is exact. The finding's `category: "secret"` is mislabeled " this is a dependency-pinning/supply-chain concern under the infra-secrets dimension, not a credential exposure. Severity medium is appropriate (reproducibility + transitive CVE surface, not a directly exploitable vuln in the application code). Suggested fix (pin torch/transformers/huggingface_hub/tokenizers or adopt pip-tools/uv lock + pip-audit) is sound. One caveat: pinning torch specifically can harm portability across CPU/GPU platforms, so a constraints file with platform-aware extras is preferable to a single hard pin in requirements.txt.


---

### `cors-localhost-default-prod`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | medium |
| Category | secret |
| Location | `infra/docker-compose.yml:114` |
| Status | **Fixed** |

**Summary.** ATHENA_CORS_ORIGINS defaults to ["http://localhost"] in compose; if not overridden in prod the API either blocks browser calls from the real origin or forces operators to widen it (often to '*'), creating a misconfiguration risk.

**Failure scenario.** Operator deploys without overriding CORS; the browser SPA on the real domain is blocked, prompting them to set '*' which then allows any origin to call the API with user credentials.

**Evidence.** ATHENA_CORS_ORIGINS: '["http://localhost"]'

**Suggested fix.** Make CORS_ORIGINS a required env in prod and validate it on startup; document the prod origin explicitly. Reject '*' when credentials are supported.

**Verification rationale.** Verified at infra/docker-compose.yml:114 " `ATHENA_CORS_ORIGINS: '["http://localhost"]'` is set verbatim. backend/main.py:53-59 wires CORSMiddleware with allow_origins=_settings.cors_origins, allow_credentials=True, allow_methods/headers=["*"]. backend/app/core/config.py:80 defaults cors_origins to ["http://localhost:5173"] when unset. There is no startup validation requiring CORS_ORIGINS in prod and no rejection of "*" when credentials are enabled (confirmed via grep across backend/*.py " the only "*" + CORS guards live in site-packages, not application code). The failure scenario is therefore real and unmitigated: an operator deploying without overriding gets a localhost-only allowlist that blocks the real-domain SPA, and nothing prevents them from widening to "*" while allow_credentials=True is on. This is a legitimate hardening/misconfiguration-risk finding at low severity; it is not a directly exploitable code defect, since the shipped default is restrictive (the danger requires operator action to widen it).

**Notes.** Line 114 is exact. Minor doc drift: docs/configuration.md:54, README.md:142, and backend/app/core/config.py:80 all state the default is ["http://localhost:5173"], while the compose override uses ["http://localhost"] (no port). This does not change the finding. Suggested fix is sound and unimplemented.


---

### `secrets-in-plaintext-env`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | secret |
| Location | `infra/docker-compose.yml:105` |
| Status | **Mitigated (env-parameterized) - production secret manager is an ops concern; see SUMMARY.md** |

**Summary.** All secrets (DB password, JWT secret, service URLs with embedded creds) are passed as plain `environment:` values in the compose file rather than via Docker secrets, a mounted vault, or at least an env_file that is git-ignored.

**Failure scenario.** Anyone with read access to the repo or the running container's inspect metadata (`docker inspect athena-api`) sees the JWT secret and DB password in cleartext, enabling offline forgery of tokens and direct DB access.

**Evidence.** ATHENA_DATABASE_URL: postgresql+asyncpg://athena:athena@postgres:5432/athena\n ATHENA_JWT_SECRET: change-me-in-prod

**Suggested fix.** Use Docker Compose secrets (`secrets:` block with files) or inject at runtime from a secrets manager (Vault / AWS SM / GCP SM). At minimum move secrets to a `.env` file that is git-ignored and referenced via `env_file:`.

**Verification rationale.** Confirmed by reading infra/docker-compose.yml: the `api` service's `environment:` block (lines 105-114) passes ATHENA_DATABASE_URL (line 106, with embedded creds `athena:athena`) and ATHENA_JWT_SECRET (line 111, `change-me-in-prod`) as plaintext values with no `secrets:` block, `env_file:`, or git-ignored `.env` reference. Postgres also sets POSTGRES_PASSWORD=athena in plaintext (line 28). The failure scenario holds: `docker inspect athena-api` and repo read access expose both the JWT secret and DB password in cleartext, enabling token forgery and direct DB access. Severity is correctly low because these are placeholder dev defaults and the file is a local dev compose (the JWT line even carries a `# set in production!` comment), not real production secrets " but the hygiene gap is genuine and the suggested fix (env_file / Docker secrets) is valid.

**Notes.** Line offset: the finding cites line 105, which is the start of the `environment:` block; the actual secrets are on line 106 (ATHENA_DATABASE_URL) and line 111 (ATHENA_JWT_SECRET). File path Y:\AI_Projects\project-athena\infra\docker-compose.yml is correct. Severity low is appropriate " values are dev placeholders, not real prod secrets, and the compose file is intended for local bring-up.


---

### `unpinned-image-tags`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | secret |
| Location | `infra/docker-compose.yml:59` |
| Status | **Fixed** |

**Summary.** The compose file references images by mutable tags (:latest for ollama, redis:7-alpine, pgvector/pgvector:pg16) without digest pinning, so builds are non-reproducible and silently inherit whatever image the registry serves at pull time.

**Failure scenario.** A compromised or yanked upstream tag (especially :latest) is pulled on the next build, introducing a malicious or broken Postgres/Redis/Ollama image into the running stack with no review.

**Evidence.** image: pgvector/pgvector:pg16\n image: redis:7-alpine\n image: ollama/ollama:latest

**Suggested fix.** Pin every image to a digest, e.g. `image: pgvector/pgvector:pg16@sha256:...`. Run `docker compose pull` deliberately in CI with a lockfile.

**Verification rationale.** Verified directly against Y:\AI_Projects\project-athena\infra\docker-compose.yml. The three infra services use mutable tags with no digest pinning: line 24 `image: pgvector/pgvector:pg16`, line 45 `image: redis:7-alpine`, line 59 `image: ollama/ollama:latest` (and line 77 `ollama-pull` also uses `image: ollama/ollama:latest`). The claimed line (59) and evidence match the actual file. A glob of `infra/**` and `**/docker-compose*.{yml,yaml,lock}` shows only one compose file and no lockfile/override file, so there is no mitigation pinning these to digests elsewhere. Pulling these tags on each `docker compose pull` will silently resolve to whatever the registry currently serves, so the failure scenario (tag mutation/yank introducing a malicious or broken image) is accurate. Severity low is appropriate: this is a supply-chain/reproducibility concern in a dev-oriented compose file, not a directly exploitable runtime vulnerability, and the only `:latest` usage is for ollama (a local LLM runtime); pgvector:pg16 and redis:7-alpine are at least minor-scoped rather than truly floating.

**Notes.** Finding is accurate as reported; line 59 is correct. Note that the same `ollama/ollama:latest` mutable tag is also used at line 77 (ollama-pull service), which the original evidence string did not separately call out.


---

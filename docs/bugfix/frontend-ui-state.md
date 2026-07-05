# Frontend UI-State Audit

_19 unique confirmed findings (+2 second-lens duplicates, +3 rejected) from an
adversarial 5-lens audit of the React SPA for "UI state not updating after an
action" / non-smooth UX. All confirmed findings fixed; production build passes._

This is a **separate pass** from the security-focused [`frontend.md`](./frontend.md)
(JWT storage, stream 401, timer leak). That audit asked "is it secure?"; this
one asks "does the UI state stay correct and feel smooth after every action?".

## Audit method

A 29-agent Workflow ran 5 review lenses in parallel — **auth-state-sync**,
**chat-stream-reconcile**, **documents-ui**, **routing-navigation**,
**css-ux-smoothness** — each producing candidate findings, then every candidate
was verified by an independent adversarial agent that tried to **refute** it
against the real code. 24 candidates → **21 confirmed**, 3 rejected. Two
confirmed findings (#13, #14) are second-lens restatements of #1 and #4 (same
root cause, surfaced by both the auth and routing lenses) and are merged here.

## Summary table

| # | Sev | Lens | ID | Location | Status |
|---|---|---|---|---|---|
| 1 | HIGH | auth/routing | [`auth-singleton-never-listens-auth-failed`](#1-auth-singleton-never-listens-auth-failed) | `frontend/src/hooks/useAuth.js:2` | Fixed |
| 2 | HIGH | chat/routing | [`new-chat-does-not-clear-store`](#2-new-chat-does-not-clear-store) | `frontend/src/pages/ChatInterface.jsx:136` | Fixed |
| 3 | MEDIUM | auth | [`refresh-token-stored-but-never-used`](#3-refresh-token-stored-but-never-used) | `frontend/src/services/apiClient.js:45` | Fixed |
| 4 | MEDIUM | chat | [`assistant-reply-flicker-on-stream-end`](#4-assistant-reply-flicker-on-stream-end) | `frontend/src/pages/ChatInterface.jsx:111` | Fixed |
| 5 | MEDIUM | chat | [`stream-errors-before-run-started-invisible`](#5-stream-errors-before-run-started-invisible) | `frontend/src/pages/ChatInterface.jsx:205` | Fixed |
| 6 | MEDIUM | routing | [`startnew-never-syncs-url`](#6-startnew-never-syncs-url) | `frontend/src/pages/ChatInterface.jsx:86` | Fixed |
| 7 | MEDIUM | routing | [`delete-active-leaves-stale-url`](#7-delete-active-leaves-stale-url) | `frontend/src/pages/ChatInterface.jsx:173` | Fixed |
| 8 | MEDIUM | documents | [`file-input-reset-only-on-success`](#8-file-input-reset-only-on-success) | `frontend/src/pages/DocumentManager.jsx:92` | Fixed |
| 9 | LOW | chat | [`fingerprint-dedup-drops-repeated-message`](#9-fingerprint-dedup-drops-repeated-message) | `frontend/src/store/chatStore.js:130` | Fixed |
| 10 | LOW | documents | [`filter-zero-empty-state-misleading`](#10-filter-zero-empty-state-misleading) | `frontend/src/pages/DocumentManager.jsx:218` | Fixed |
| 11 | LOW | documents | [`initial-load-flash-no-documents`](#11-initial-load-flash-no-documents) | `frontend/src/pages/DocumentManager.jsx:17` | Fixed |
| 12 | LOW | documents | [`polling-never-paused-when-hidden`](#12-polling-never-paused-when-hidden) | `frontend/src/pages/DocumentManager.jsx:68` | Fixed |
| 13 | LOW | documents | [`delete-flow-no-feedback`](#13-delete-flow-no-feedback) | `frontend/src/pages/DocumentManager.jsx:108` | Fixed |
| 14 | LOW | documents | [`upload-error-renders-array-detail`](#14-upload-error-renders-array-detail) | `frontend/src/pages/DocumentManager.jsx:96` | Fixed |
| 15 | LOW | css | [`status-pill-no-transition`](#15-status-pill-no-transition) | `frontend/src/styles.css:171` | Fixed |
| 16 | LOW | css | [`button-state-no-transition`](#16-button-state-no-transition) | `frontend/src/styles.css:29` | Fixed |
| 17 | LOW | css | [`modal-no-fade`](#17-modal-no-fade) | `frontend/src/styles.css:184` | Fixed |
| 18 | LOW | css | [`no-focus-visible-on-buttons`](#18-no-focus-visible-on-buttons) | `frontend/src/styles.css:29` | Fixed |
| 19 | LOW | css | [`loading-screen-undefined-muted-var`](#19-loading-screen-undefined-muted-var) | `frontend/src/App.jsx:31` | Fixed |

[Rejected claims](#rejected-claims) are listed at the end.

---

## #1 `auth-singleton-never-listens-auth-failed`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Lenses | auth-state-sync, routing-navigation (duplicate) |
| Location | `frontend/src/hooks/useAuth.js:2` |
| Status | **Fixed** |

**Summary.** The `useAuth` singleton never listens for `athena:auth-failed`, so
after any post-bootstrap 401 the singleton's `_state.token` stays stale while
`localStorage` is cleared — locking the user in an infinite redirect loop
between `/login` and the protected page, unable to even see the login form.

**Failure scenario.** User on `/chat` with an expired JWT. `loadConversations`
→ 401 → `_handle401` clears `localStorage` and dispatches `AUTH_EVENT`.
`AuthBoundary` navs to `/login?next=/chat`. `Login.jsx`'s effect sees
`ready && token` (token still truthy in the stale singleton) → navs back to
`/chat`. `Protected` renders `ChatInterface` → `loadConversations` → 401 →
repeat. Bounded only by network round-trip latency, but non-terminating; the
only escape is a hard reload that re-reads the now-empty `localStorage`.

**Root cause.** `_state.token` is mutated only inside `login`/`register`/
`logout`/`refresh`/`ensureBootstrapped`, and `ensureBootstrapped` early-returns
once `ready` is true. `useAuth.js` did not import `AUTH_EVENT` and registered no
listener, so the singleton and `localStorage` diverged after any 401.

**Fix.** Imported `AUTH_EVENT` from `apiClient.js` and registered a module-level
listener that clears the singleton when the event fires:

```js
if (typeof window !== 'undefined') {
  window.addEventListener(AUTH_EVENT, () => {
    if (_state.token) setState({ token: null, user: null, ready: true });
  });
}
```

Registered at module load (before `AuthBoundary` mounts), so it fires
synchronously during `dispatchEvent`, before `AuthBoundary`'s listener —
flipping `token` to `null` so `Login`'s effect no-ops and `Protected`
redirects. No circular import (`apiClient` does not import `useAuth`). The
`if (_state.token)` guard avoids a redundant emit.

**Verification.** Traced the full loop across `useAuth.js`, `apiClient.js`,
`main.jsx`, `Login.jsx`, `App.jsx`, `ChatInterface.jsx`, `chatStore.js`. Grep
confirmed no `AUTH_EVENT` listener existed in `src/` except `main.jsx`
(`AuthBoundary`). The `setTimeout(0)` hard-redirect fallback in `_handle401`
was a no-op every iteration because `AuthBoundary`'s synchronous nav flips
`location.pathname` to `/login` before the timer fires.

---

## #2 `new-chat-does-not-clear-store`

| Field | Value |
|---|---|
| Severity | **HIGH** |
| Lenses | chat-stream-reconcile, routing-navigation (duplicate) |
| Location | `frontend/src/pages/ChatInterface.jsx:136` |
| Status | **Fixed** |

**Summary.** Clicking "+ New chat" navigated the URL to `/chat` but never reset
the Zustand store — the old conversation's transcript stayed on screen and new
messages were persisted into the old conversation (a data-integrity defect).

**Failure scenario.** User on `/chat/A` clicks "+ New chat". `onNewChat` called
`nav('/chat')` only; `conversationId` became `undefined`, but the
`openConversation` effect is guarded by `if (conversationId)` and did nothing,
and nothing else cleared the store. Because Zustand state lives outside React
(and `/chat` and `/chat/:id` render the same `ChatInterface` at the same tree
position with no key, so no remount), `active` still equalled A and `messages`
still held A's content. The empty-state bubble (`!active`) was suppressed.
When the user typed and sent, `onSend` did `let convId = active` (= A), skipped
`startNew()`, and `stream.send` posted with `conversation_id: A` — the new
message was written to conversation A, not a fresh one.

**Root cause.** `onNewChat` changed only the URL. There was no store action that
cleared `active`/`messages`/`pending` without a server call, and no effect
resetting state when `conversationId` became absent.

**Fix.** Added a `clearActive()` store action to `chatStore.js`
(`set({ active: null, messages: [], pending: new Set() })`), and in
`ChatInterface.jsx` added an effect that clears the store (and resets the
stream) whenever `conversationId` is absent — covering both "+ New chat" and a
direct navigation to `/chat`:

```js
useEffect(() => {
  if (conversationId) {
    openConversation(conversationId);
  } else {
    streamReset();
    clearActive();
  }
}, [conversationId, openConversation, clearActive, streamReset]);
```

Now the empty-state renders and `onSend` falls through to `startNew()`.

**Verification.** Confirmed the full causal chain. `onNewChat` only called
`nav('/chat')`; the `openConversation` effect skipped for falsy
`conversationId`; the store is global so remount does not clear it; `onSend`
read `active` (= A) and posted to A. Severity HIGH: data written to the wrong
conversation, not merely cosmetic.

---

## #3 `refresh-token-stored-but-never-used`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Lens | auth-state-sync |
| Location | `frontend/src/services/apiClient.js:45` |
| Status | **Fixed** |

**Summary.** The refresh token (`athena_refresh`) is stored on login/register
and `authService.refresh` exists, but nothing ever calls it — every
access-token expiry bounced the user to `/login` and discarded in-memory
chat/document state despite a perfectly valid refresh token.

**Failure scenario.** User mid-session on `/chat`; access token expires; next
request returns 401; `_handle401` immediately `clearTokens()` (wiping **both**
tokens) + dispatches `AUTH_EVENT` → forced re-login, losing in-memory state.
`authService.refresh` and `useAuth.refresh` were wired but never invoked from
the 401 path or anywhere else (grep for `.refresh(` returned no matches).

**Root cause.** `_handle401` treated every 401 as terminal with no attempt to
redeem the refresh token first.

**Fix.** Added `_tryRefresh()` (redeems `athena_refresh` via a **raw `fetch`**
to `/auth/refresh` — not `apiClient.post`, so a 401 from refresh can never
recurse into `_handle401`), with a module-level `_refreshing` promise to
coalesce concurrent 401s onto a single refresh call. Made `_handle401` `async`
and return `true` when refresh succeeded (caller retries) / `false` when
terminal (clear + dispatch). Rewrote `request()`, `upload()`, and `stream()`
with an inner `attempt()` that reads the token fresh each call, so a retry
after refresh picks up the new access token:

```js
let res = await attempt();
if (res.status === 401) {
  const refreshed = await _handle401('request');
  if (refreshed) res = await attempt();      // retry once
  if (res.status === 401) throw unauthorized;
}
```

**Verification.** Confirmed `apiClient._handle401` (lines 45-60) only
`clearTokens()` + dispatch + `setTimeout` fallback; `authService.refresh`
(lines 21-27) is real wired infrastructure; grep returned no callers; all three
401 sites (request, upload, stream) routed through `_handle401` with no refresh
attempt. Severity MEDIUM: frequent forced re-login with state loss, but no data
corruption or security impact.

---

## #4 `assistant-reply-flicker-on-stream-end`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Lens | chat-stream-reconcile |
| Location | `frontend/src/pages/ChatInterface.jsx:111` |
| Status | **Fixed** |

**Summary.** The assistant reply vanished for the duration of the
`refreshActive` round-trip on every turn — a guaranteed flash because the
stream bubble unmounted (`runId` cleared) before the persisted message arrived.

**Failure scenario.** Send a message; the assistant streams its reply via the
bubble gated on `stream.runId`. `useChatStream.send`'s `finally` runs
`setRunId(null)` and `setDone(true)` **before** `await stream.send(...)` in
`onSend` resolves. `onSend` then hits `await refreshActive()` (a network GET).
During that round-trip: `stream.runId` is null → stream bubble unmounted, and
`messages` does not contain the assistant turn (only the optimistic user
message was appended). So the user sees **neither** the stream bubble **nor**
the persisted assistant message until `refreshActive` resolves.

**Root cause.** `runId` was cleared in the hook's `finally` before the consumer
reconciled with the server, and the assistant text lived only in
`stream.text` — nothing carried it into `messages` before `runId` was nulled.

**Fix.** In `onSend`, after `await stream.send(...)` and before
`await refreshActive()`, optimistically append the assistant turn from the
streamed text:

```js
if (sentOk && stream.text) {
  appendMessage({ id: tempId(), seq: 0, role: 'assistant',
    content: stream.text, citations: stream.citations,
    used_tools: stream.usedTools, created_at: new Date().toISOString() });
}
```

The existing fingerprint dedup in `appendMessage`/`refreshActive`
(`role|content`) drops this local copy once the server returns the real
assistant message, so there is no duplication.

**Verification.** Traced the async timing: `finally` runs before the async
function's promise resolves, so `await stream.send` resolves only after
`runId` is null; the gap between bubble unmount and `refreshActive` resolving
is a guaranteed empty window on every turn. Severity MEDIUM: cosmetic flicker,
no data loss (the message always reappears).

---

## #5 `stream-errors-before-run-started-invisible`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Lens | chat-stream-reconcile |
| Location | `frontend/src/pages/ChatInterface.jsx:205` |
| Status | **Fixed** |

**Summary.** Stream errors that occur before `RUN_STARTED` (network drop, 401,
4xx/5xx on the POST, abort before the reader loop) were never displayed — the
error bubble was nested inside the `stream.runId` guard, and those failures
leave `runId` null, so the app appeared to hang silently.

**Failure scenario.** Stop the backend, type a message, press Send. The failure
happens pre-flight: `apiClient.stream` throws or `!resp.ok`. Both paths call
`setError(...)` + `setDone(true)` but never `setRunId(...)` (runId stays null
from `reset()`). The error bubble JSX was a child of `{stream.runId && (...)}`,
so with runId null it never rendered. The user sees their optimistic user
message alone with no reply and no error — a silent hang.

**Root cause.** The error bubble was conditionally rendered as a child of the
`stream.runId` guard.

**Fix.** Hoisted the error bubble out of the `stream.runId` block to be a
sibling inside `.messages`, rendering on `stream.error` regardless of `runId`:

```jsx
{stream.runId && (<>…thinking/text/tools…</>)}
{stream.error && (
  <div className="bubble" style={{ borderColor: 'var(--danger)' }} role="alert">
    Error: {stream.error}
  </div>
)}
```

**Verification.** Confirmed in `useChatStream.js` that the two pre-flight
failure paths (lines 87-97, 99-105) set `error`/`done` but never `runId`, and
in `ChatInterface.jsx` that the only error UI was nested inside the `runId`
guard at line 205. Severity MEDIUM: real silent-error UX defect, no data loss.

---

## #6 `startnew-never-syncs-url`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Lens | routing-navigation |
| Location | `frontend/src/pages/ChatInterface.jsx:86` |
| Status | **Fixed** |

**Summary.** The first send from the `/chat` empty state created a conversation
via `startNew()` but never synced its id into the URL, so a refresh dropped the
active conversation (only recoverable via the sidebar).

**Failure scenario.** User on `/chat` (no `conversationId`, `active=null`).
`onSend` finds `active=null`, calls `startNew()` which POSTs
`/chat/conversations`, sets `active=convId`, returns `convId`. The conversation
appears in the sidebar, but the URL stays `/chat`. On refresh, `useParams`
returns nothing, the `openConversation` effect is skipped, the store
reinitializes empty, and the empty-state bubble renders — conversation C is
only reachable by clicking it in the sidebar.

**Root cause.** `startNew` in the store creates the conversation and sets
`active` but has no router access. `onSend` consumed the returned `convId` but
did not navigate.

**Fix.** In `onSend`, after creating a new conversation, sync the URL:

```js
const wasNew = !active;
let convId = active;
if (!convId) convId = await startNew();
if (!convId) return;
if (wasNew) nav(`/chat/${convId}`, { replace: true });
```

`replace: true` avoids leaving the bare `/chat` entry in history. The
subsequent `openConversation` effect is a no-op because the guard
`active === id && messages.length > 0` is satisfied.

**Verification.** Confirmed `onSend` never called `nav` after `startNew`, and
the store's `startNew` cannot navigate. No data loss (the conversation is
persisted and in the sidebar), but the active view is dropped on refresh.
Severity MEDIUM.

---

## #7 `delete-active-leaves-stale-url`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Lens | routing-navigation |
| Location | `frontend/src/pages/ChatInterface.jsx:173` |
| Status | **Fixed** |

**Summary.** Deleting the **active** conversation reset `active`/`messages` in
the store but left the URL at `/chat/:deletedId`, so a refresh or back-nav
triggered a 404 refetch and rendered a blank panel.

**Failure scenario.** User on `/chat/A` clicks × on conversation A.
`deleteConversation(A)` DELETEs, removes A from `conversations`, and (active===A)
sets `active=null, messages=[], pending=new Set()`. The URL is never changed.
On refresh/back-nav to `/chat/A`, `openConversation(A)` fires, the no-op guard
is false (active=null), so it GETs `/chat/conversations/A` → 404 → store error,
leaving `active=A` with `messages=[]`. `ChatInterface` never renders the store's
`error` field, so the visible result is a blank chat panel — no bubbles, no
empty-state prompt, no error.

**Root cause.** `deleteConversation` reset state when the active conv was
deleted, but neither the store nor `ChatInterface` navigated away from the
now-invalid URL.

**Fix.** In the sidebar delete handler, navigate when the deleted conversation
was active:

```js
onClick={async () => {
  const wasActive = c.id === active;
  await deleteConversation(c.id);
  if (wasActive) nav('/chat', { replace: true });
}}
```

(Belongs in the component, not the store, because the store has no router
access.)

**Verification.** Confirmed `deleteConversation` (chatStore.js 109-120) resets
state but never updates the URL, and the `openConversation` effect deps did not
re-fire on deletion. Severity MEDIUM: broken/blank state on refresh/back-nav,
no crash or data loss.

---

## #8 `file-input-reset-only-on-success`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Lens | documents-ui |
| Location | `frontend/src/pages/DocumentManager.jsx:92` |
| Status | **Fixed** |

**Summary.** The hidden file input's value was reset only inside `try` after a
successful upload, so a failed upload left the input holding the same file —
and browsers don't fire `change` for an unchanged value, so the user could not
re-pick the same file to retry without first choosing a different file or
reloading.

**Failure scenario.** User picks `report.pdf`; upload fails (413/500/network).
The `catch` sets `err` but never resets `e.target.value`; `finally` only clears
`uploading`. The input still holds `report.pdf`, so clicking Upload and
re-selecting the same file fires no `change` event → `onPick` does not run.

**Root cause.** `e.target.value = ''` was inside `try`, after
`await docService.upload(f)`, so it ran only on the success path.

**Fix.** Moved the reset to the top of `onPick`, right after reading `f` and
before `setUploading`, so it runs on every path regardless of upload outcome.

**Verification.** Confirmed the reset was on line 92 inside `try` after the
await; `catch`/`finally` did not reset. Severity MEDIUM: real stuck-state UX
trap with a non-obvious silent failure, but not data loss and workarounds exist.

---

## #9 `fingerprint-dedup-drops-repeated-message`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | chat-stream-reconcile |
| Location | `frontend/src/store/chatStore.js:130` |
| Status | **Fixed** |

**Summary.** The global `role|content` fingerprint dedup in `appendMessage`
silently dropped a second genuinely-distinct send whose text happened to match
an earlier confirmed message — the optimistic append vanished until the server
round-trip resolved.

**Failure scenario.** Send "hello"; let the turn complete so `refreshActive`
replaces the optimistic copy with the server-persisted `user|hello` and clears
it from `pending`. Send "hello" again: `appendMessage` dedups against the full
list, the confirmed `user|hello` matches, and the optimistic append is dropped.
`setInput('')` clears the composer, so the second "hello" is absent from both
composer and transcript until `refreshActive` resolves. Transient only; no data
loss.

**Root cause.** `fingerprint` is `role|content` with no sequence/timestamp, so
it cannot distinguish two separate messages that share content. The global
check was intended to prevent the same optimistic insert being added twice, but
it also suppressed a second distinct send of identical text.

**Fix.** Removed the global `list.some((m) => fingerprint(m) === fp)` check
(kept the id-based dedup and the `pending`-set add). Rapid double-clicks are
already blocked by `sendingRef`/`stream.runId` in `ChatInterface` and by the id
check; the `pending`-set logic in `refreshActive` is the only dedup actually
required to collapse a local copy once the server confirms.

**Verification.** Confirmed the check at chatStore.js:130 ran against the full
list, not just pending. Severity LOW: transient render gap, no data loss (the
server receives and persists the second message).

---

## #10 `filter-zero-empty-state-misleading`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | documents-ui |
| Location | `frontend/src/pages/DocumentManager.jsx:218` |
| Status | **Fixed** |

**Summary.** The empty-state card always printed "No documents yet. Upload…"
even when documents existed but none matched the active status filter —
misleading copy that directed the user to upload, which would not change the
filtered result.

**Failure scenario.** User has 10 indexed documents, sets the filter to
"Failed". `visible` becomes `[]`; the empty-state branch fires on
`visible.length === 0` and renders the upload prompt. The filter dropdown stays
usable so the user can recover, but the copy is wrong.

**Root cause.** The empty-state branch was unconditional on
`visible.length === 0` and never inspected `filter` or `docs.length`.

**Fix.** Branched on whether a filter is active: if `docs.length > 0` and a
filter is set, render `No documents match the "{filter}" filter.`; only show
the "No documents yet. Upload…" copy when `docs.length === 0`.

**Verification.** Confirmed `visible = filter ? docs.filter(...) : docs` and the
empty-state gate keyed only off `visible.length === 0`. Severity LOW:
misleading-copy/cosmetic; the dropdown and the "{total} documents." line let the
user recover instantly.

---

## #11 `initial-load-flash-no-documents`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | documents-ui |
| Location | `frontend/src/pages/DocumentManager.jsx:17` |
| Status | **Fixed** |

**Summary.** On mount, `DocumentManager` rendered with `docs=[]` and no loading
state, so the "No documents yet" empty-state (plus "0 documents.") flashed for
the hundreds of ms before the first fetch resolved — even for accounts with
many documents.

**Failure scenario.** `docs` initialized to `[]`, `total` to 0, no
loading/`hasLoadedOnce` state. The empty-state branch keyed off
`visible.length === 0`. The initial fetch is async via `useEffect`/`tick()`, so
the first paint always shows the empty state until the first list response
arrives.

**Root cause.** No loading / `loadedOnce` state; `docs=[]` was indistinguishable
from a genuinely empty account on the pre-fetch render.

**Fix.** Added `const [loadedOnce, setLoadedOnce] = useState(false)` and set it
`true` in `load()`'s `finally` (success or failure). The empty-state branch now
renders "Loading documents…" while `!loadedOnce`, and only shows "No documents
yet…" once the first load has completed.

**Verification.** Confirmed no loading state existed and the empty-state keyed
solely off `visible.length === 0`. Severity LOW: brief, self-correcting visual
flash, no functional impact.

---

## #12 `polling-never-paused-when-hidden`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | documents-ui |
| Location | `frontend/src/pages/DocumentManager.jsx:68` |
| Status | **Fixed** |

**Summary.** The line-68 comment promised polling pauses when the tab is hidden,
but the `visibilitychange` handler only re-fetched on becoming visible and
never stopped the self-scheduling `tick` loop — so polling continued in
background tabs (every 2s when a doc was busy), the opposite of the promised
battery/bandwidth savings.

**Failure scenario.** User opens `DocumentManager`, switches to another tab for
10 minutes. The `onVis` handler only handles the "became visible" case; there
is no `hidden` branch clearing the timer, and `timer` is a closure-local the
handler cannot reach. The self-scheduling `tick` keeps running (browser
background throttling mitigates but does not eliminate it).

**Root cause.** The visibility handler only handled the visible case; the tick
loop had no `document.hidden` guard.

**Fix.** Guarded the top of `tick`: when `document.hidden`, skip the fetch and
just re-schedule. The `onVis` handler still fires a fresh load on return.

```js
const tick = async () => {
  if (cancelled) return;
  if (!document.hidden) {
    await loadRef.current?.();
    if (cancelled) return;
  }
  const anyBusy = docsRef.current.some(/* … */);
  timer = setTimeout(tick, anyBusy ? POLL_FAST_MS : POLL_SLOW_MS);
};
```

**Verification.** Confirmed the handler had no hidden branch and `tick`
self-scheduled with no `document.hidden` guard. Severity LOW: broken
optimization promise; polling itself remained correct.

---

## #13 `delete-flow-no-feedback`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | documents-ui |
| Location | `frontend/src/pages/DocumentManager.jsx:108` |
| Status | **Fixed** |

**Summary.** The delete flow closed the modal optimistically before the DELETE
resolved, had no in-flight indicator, and on error showed a generic banner with
no indication of which document failed — the row's Delete button stayed
clickable, allowing a duplicate concurrent delete.

**Failure scenario.** User clicks Delete, confirms. `onConfirmDelete` calls
`setPendingDelete(null)` (closing the modal) **before** the await. No
`deleting` state, so the row shows normally and its Delete button is fully
clickable again (re-opening the modal / duplicate concurrent DELETE). On error,
`setErr(e.message)` shows a generic banner with no filename, and `load()` is in
the try block so the list never refreshes on error.

**Root cause.** `onConfirmDelete` closed the modal optimistically and had no
in-flight tracking; the catch only set a generic banner.

**Fix.** Added a `deletingId` state. Keep the modal open with the Delete button
disabled + labelled "Deleting…" while in flight; disable the row's Delete button
when `deletingId === d.id`; move `setPendingDelete(null)` to the success path;
on error include the filename: ``Could not delete ${doc.filename}: ${e.message}``.

**Verification.** Confirmed `setPendingDelete(null)` ran before the await, no
deleting state existed, and the catch only set `e.message`. Severity LOW: UX
feedback defect; worst realistic outcome is a redundant DELETE that 404s.

---

## #14 `upload-error-renders-array-detail`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | documents-ui |
| Location | `frontend/src/pages/DocumentManager.jsx:96` |
| Status | **Fixed** |

**Summary.** The upload error handler preferred `e2.body.detail`, which for a
FastAPI 422 is an array of `{loc,msg,…}` objects — passing an array of objects
as a React child throws "Objects are not valid as a React child" (or renders
junk), and the prepared human-readable `e2.message` was never shown.

**Failure scenario.** User uploads a file the backend rejects with a 422.
`apiClient._readError` flattens the array `detail` into `err.message` but
preserves the raw array on `err.body.detail`. `setErr(e2?.body?.detail || …)`
checks `body.detail` first; the truthy array wins; the render `{err}` throws.

**Root cause.** The catch checked `e2.body.detail` first, bypassing
`_readError`'s flattening.

**Fix.** Use the already-flattened message as the primary source, and only fall
back to `body.detail` when it is a string:

```js
const detail = e2?.body?.detail;
const msg = (typeof detail === 'string' ? detail : null) || e2?.message || 'upload failed';
setErr(msg);
```

**Verification.** Confirmed line 96 read `e2?.body?.detail || e2?.message || …`
and `_readError` leaves `err.body.detail` as the raw array. Severity LOW:
error-path only, no data loss, but the failure mode (render-time throw) is
worse than cosmetic.

---

## #15 `status-pill-no-transition`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | css-ux-smoothness |
| Location | `frontend/src/styles.css:171` |
| Status | **Fixed** |

**Summary.** `.status-pill` (and the `.status-*` variants) defined static
background/color but no `transition`, so a document moving
uploaded→processing→indexed via polling snapped color instantly instead of
transitioning.

**Fix.** Added `transition: background-color 200ms ease, color 200ms ease;` to
the `.status-pill` rule.

**Verification.** Confirmed lines 171-181 declared only static styles with no
`transition`. Severity LOW: purely cosmetic polish; the correct color still
shows.

---

## #16 `button-state-no-transition`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | css-ux-smoothness |
| Location | `frontend/src/styles.css:29` |
| Status | **Fixed** |

**Summary.** The base `button` rule declared no `transition`, so hover
background changes and disabled↔enabled opacity changes were instantaneous —
abrupt state-change feedback (including the disabled→enabled snap the user
waits on after upload finishes).

**Fix.** Added `transition: background-color 150ms ease, opacity 150ms ease, border-color 150ms ease;` to the base `button` rule (border-color also covers
the `.secondary` variant). Also added a matching `transition: border-color 150ms ease;` to `input, textarea, select` so the focus border animates.

**Verification.** Confirmed `button` (lines 29-37) had no `transition`;
`:hover` changes background, `:disabled` changes opacity. Severity LOW:
cosmetic.

---

## #17 `modal-no-fade`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | css-ux-smoothness |
| Location | `frontend/src/styles.css:184` |
| Status | **Fixed** |

**Summary.** `.modal-backdrop` and `.modal` declared no `transition` or
`@keyframes`, and React mounts/unmounts the nodes synchronously, so the delete
modal appeared/disappeared hard at full opacity — a jarring open/close.

**Fix.** Added entry animations:

```css
@keyframes modalIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes panelIn { from { opacity: 0; transform: translateY(8px) scale(0.98); }
                     to   { opacity: 1; transform: none; } }
.modal-backdrop { …; animation: modalIn 160ms ease; }
.modal          { …; animation: panelIn 160ms ease; }
```

(Exit fade would require a mount-delay library or CSS view-transitions; the
entry fade alone removes the jarring open.)

**Verification.** Confirmed no `@keyframes`/`animation`/`transition` existed
anywhere in the file and the modal is a plain conditional mount. Severity LOW:
cosmetic.

---

## #18 `no-focus-visible-on-buttons`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | css-ux-smoothness |
| Location | `frontend/src/styles.css:29` |
| Status | **Fixed** |

**Summary.** The stylesheet defined `:focus` styling only for form controls
(`outline: none; border-color: var(--accent)`) and provided no `:focus-visible`
rule for `button` or `a`, so keyboard focus on buttons/links relied on the
browser default — low-contrast against the accent-colored button background and
inconsistent with the input focus treatment.

**Fix.** Added `button:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }` so keyboard focus matches the input
focus treatment.

**Verification.** Confirmed the only focus styling was scoped to form controls
and no button/anchor focus rule existed. Severity LOW: keyboard focus
affordance degraded/inconsistent, not eliminated.

---

## #19 `loading-screen-undefined-muted-var`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Lens | css-ux-smoothness |
| Location | `frontend/src/App.jsx:31` |
| Status | **Fixed** |

**Summary.** `Protected`'s loading screen used `color: 'var(--muted, #888)'`,
but `--muted` is not declared in `:root`, so the color fell back to the
hardcoded `#888` instead of the design system's `--text-dim` (`#94a3b8`) — a
color mismatch decoupled from palette changes.

**Fix.** Changed the inline style to `color: 'var(--text-dim)'` to reuse the
existing dim-text token.

**Verification.** Confirmed `:root` defines `--text-dim` (`#94a3b8`) and no
`--muted` token. Severity LOW: purely cosmetic on a transient loading screen.

---

## Rejected claims

Three candidate findings were **refuted** by the adversarial verify stage and
were **not** fixed. Recorded here so the audit trail is complete.

### R1. `sendingRef` is a ref so the Send button is not disabled before RUN_STARTED

**Rejected.** In the common case (active conversation exists, so `startNew()`
is not awaited), `onSend` runs synchronously from the click through to the
`await` at `stream.send`, queueing `appendMessage` + `setInput('')`. React 18
automatic batching flushes at the await, by which point `sendingRef.current` is
already `true`, so the re-render evaluates
`disabled={sendingRef.current || stream.runId != null}` to `true` — the button
**is** visually disabled before `RUN_STARTED`. The label does stay "Send" (it
reads only `stream.runId`), but the disabled state already gives feedback. The
double-click functional guard also holds in this case. Cosmetic-only, not a
defect.

### R2. `loadRef.current = load` assigned during render (ref mutation as side effect)

**Rejected.** The assignment is idempotent — each render overwrites
`loadRef.current` with the latest `load` closure, which is exactly the intended
design so the polling effect and `visibilitychange` handler always invoke the
current closure. The claim itself concedes "harmless here." No observable
UI-state inconsistency is produced (the ref is not read by any effect during
the same render pass). A code-quality/lint note, not a UI-state defect.

### R3. `.bubble` has no entrance transition; new chat messages pop in

**Rejected.** Rendering messages at full opacity is correct, expected behavior;
an entrance animation is an optional enhancement, not a correctness or state
bug. The streaming claim was also inaccurate — during streaming the assistant
bubble is mounted once and its text is updated in place, so bubbles do not
"appear mid-run" repeatedly. A feature request framed as a bug.

---

## Verification of fixes

After all fixes were applied, the frontend production build was run:

```
npm run build --prefix frontend
→ vite build → ✓ 33 modules transformed → ✓ built in 519ms
  dist/assets/index-*.css  4.37 kB
  dist/assets/index-*.js  188.17 kB
```

No compile/transform errors. (The `node.exe` / `NativeCommandError` noise in
the PowerShell capture is just the shell wrapping vite's stderr deprecation
warnings, not build errors.)
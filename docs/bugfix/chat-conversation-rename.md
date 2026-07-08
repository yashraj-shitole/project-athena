# Conversation rename + auto-naming from the first query

_A feature requested 2026-07-06: add a rename control in the chat
sidebar (before the delete button), and name a new conversation from
the first 100 characters of the user's first query. The implementation
was hardened by a 4-dimension adversarial review Workflow (backend /
frontend-state / regression / a11y-layout, each finding verified
against the real code); every confirmed finding below was applied and
re-verified (backend pytest + frontend build green)._

## Summary

| Concern | Decision |
|---|---|
| Rename control placement | Pencil button, absolute `right-8`, immediately left of the delete button (`right-1`), in the conversation row. |
| New-conversation name | First 100 chars of the user's first query, trimmed — sent on the `POST /chat/conversations` create call so the server's `stream_turn` sees an existing conversation and leaves the title untouched. Falls back to `"New conversation"` when no seed. |
| Rename round-trip | `PATCH /chat/conversations/{id}` with `{ title }`; the row is patched in place (title + server-bumped `updated_at`), **not** reordered, so it doesn't jump out from under the user mid-edit. |
| Title cap | 100 chars, enforced in three places: the `ConversationRename` schema (`StringConstraints(strip_whitespace=True, min_length=1, max_length=100)`), `chatStore.startNew`/`renameConversation` (`.trim().slice(0, 100)`), and `agent._ensure_conversation` (`((title_seed or "").strip()[:100] or "New conversation")`). |

## Backend

- `backend/app/schemas/conversation.py` — `ConversationRename(RequestBase)`:
  `title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]`.
  `extra="forbid"` (inherited) rejects smuggled `user_id`/`id` (mass-assignment
  defense, H-22). Strip-then-length means `"   "` 422s and 101 chars 422s.
- `backend/app/api/chat.py` — `PATCH /chat/conversations/{conversation_id}`
  (`rename_conversation`): loads by `id` **and** `user_id == user_id` (the
  same ownership filter `get`/`delete` use, on top of the RLS policy set
  upstream by `DbSession`). A cross-tenant rename is a **404, not 403**, so
  the existence of another user's conversation is not leaked. Sets
  `conv.title = payload.title`, commits, refreshes, returns `ConversationPublic`
  with `message_count=len(conv.messages or [])`. The path `conversation_id` is
  authoritative — a body `id` is refused by the schema.
- `backend/app/services/orchestrator/agent.py` — `_ensure_conversation` title
  seed changed from `(title_seed or "New conversation")[:120]` to
  `((title_seed or "").strip()[:100] or "New conversation")`, matching the UI
  and schema cap.

## Frontend

- `frontend/src/store/chatStore.js`
  - `startNew(firstMessage)` posts `title = seed || 'New conversation'`
    where `seed = (firstMessage||'').trim().slice(0,100)`.
  - `renameConversation(id, title)` trims/caps, `PATCH`es, patches the row
    in place. On error it sets `store.error` **and re-throws** so the caller
    can surface the failure (previously swallowed → silent revert).
- `frontend/src/pages/ChatInterface.jsx` (`ChatSidebar` + `RenameInput`)
  - Pencil rename button before the Trash2 delete button.
  - Inline edit: a plain `<div>` (not a `<form>`) wraps `RenameInput` so
    Enter is handled only by the input's `onKeyDown` (no double-submit).
  - `RenameInput`: autofocus + select-on-mount; Enter or blur commits;
    Escape cancels; a `committedRef` guard prevents the unmount-blur from
    firing commit a second time; `maxLength=100`.

## Adversarial review — confirmed findings applied

The review surfaced 11 confirmed/plausible findings; the fixes below are
all in `ChatInterface.jsx` / `chatStore.js` unless noted.

1. **[medium] Invisible buttons intercepted clicks.** The hover-revealed
   rename/delete buttons were `opacity-0` but not `pointer-events-none`, so
   the invisible buttons ate clicks across the right ~56px of each row
   (clicking a long title's ellipsis triggered rename/delete instead of
   navigating). Fix: `pointer-events-none` + `group-hover/conv:pointer-events-auto`
   + `group-focus-within/conv:pointer-events-auto` + `[@media(hover:none)]:pointer-events-auto`.
2. **[high] Rename unreachable on touch; [high] no keyboard focus indicator.**
   Hover-only reveal (`opacity-0 group-hover/conv:opacity-100`) made the
   buttons invisible on touch devices and to sighted keyboard users (focused
   but `opacity-0`). Fix: also reveal on `group-focus-within/conv:opacity-100`
   and `[@media(hover:none)]:opacity-100` (Tailwind v3.4 JIT supports the
   arbitrary media variant — confirmed against existing `[@media(hover:none)]`
   / `focus-within:` usages in the repo).
3. **[medium] Silent rename failure.** A failed `PATCH` previously set
   `store.error` (never rendered) and silently reverted the row. Fix:
   `chatStore.renameConversation`/`deleteConversation` re-throw;
   `ChatInterface` imports `useToast` and toasts `Could not rename…` /
   `Could not delete…` on error. `onDelete` now only navigates to `/chat`
   on a successful delete.
4. **[medium] Focus dropped to `<body>` after edit.** `setEditingId(null)`
   unmounted the focused input, stranding keyboard users. Fix: a
   `focusRestoreId` state set in `commitRename`/`cancelRename`; the row's
   `<Link>` reclaims focus via a ref callback when it remounts, then clears
   the id.
5. **[low] Editing an inactive row looked active; [nit] font-weight flicker.**
   The edit div always used `bg-surface-2` (the active-row fill) and the
   input always used weight 400. Fix: the edit div + `RenameInput`'s
   `textClassName` now mirror the row's `isActive ? 'bg-surface-2 text-ink
   font-medium' : 'text-ink-dim'`.
6. **[low] Long-title occlusion.** The Link had only `pr-1`, so a 100-char
   title's ellipsis landed under the hover-revealed buttons. Fix: `pr-14`
   on the Link reserves the 56px button strip so titles ellipsize before it.
7. **[nit] Stale docs.** `docs/architecture/database.md` and `docs/phase-2.md`
   said the title was "truncated to 120"; updated to 100.
8. **[low] No router-level test for the rename endpoint.** Existing
   conversation endpoints are integration-only, but the rename endpoint's
   ownership/404 behavior is security-critical. Added
   `backend/tests/test_chat_conversation_api.py` (stub-session unit tests,
   `test_model_router` pattern): success writes the stripped title +
   returns `ConversationPublic` with the right `message_count`; cross-tenant
   is 404 (not 403) with no commit; not-found for the owner is 404.

A review-suggested but **not applied** item: a confirmation dialog before
delete. The user's request was scoped to rename; delete-without-confirm is
pre-existing behavior unchanged by this feature, so it was left alone to
respect scope.

## Verification

- Backend: `pytest tests/test_chat_conversation_api.py
  tests/test_schemas_mass_assignment.py` → 21 passed. Full unit suite →
  261 passed, 17 failed (the documented pre-existing greenlet/py3.14 +
  lockout-ALTER-TABLE + tool_call-assertion set; no new regressions).
- Frontend: `npm run build` → `✓ built` (Tailwind compiled the new
  `[@media(hover:none)]` / `group-focus-within/conv:` variants without
  error).
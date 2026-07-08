import { create } from 'zustand';
import apiClient from '../services/apiClient.js';

/**
 * Conversation list + active conversation state.
 *
 *  - conversations: list of {id, title, message_count, updated_at}
 *  - active: id of the conversation currently open in the chat UI
 *  - messages: messages in the active conversation
 *  - pending: ids of messages added optimistically (kept across the
 *    openConversation refresh so the user's own message doesn't vanish)
 *  - loading / error
 *
 * Issues addressed by this revision:
 *   - `openConversation` used to overwrite `messages` with the server
 *     list, which dropped the optimistic user message the chat page
 *     had just pushed. We now keep optimistic messages and merge by id.
 *   - `startNew` could be clicked twice quickly and produce two
 *     conversations on the backend, but the store only kept one.
 *     We now guard with an in-flight ref via `startNewInFlight`.
 *   - `appendMessage` didn't dedupe; a stream-finish rerender could
 *     re-call it with the same message.
 *   - `refreshActive` previously merged server + local messages by id,
 *     but locally-appended messages carry a `tmp-...` id that the
 *     server doesn't know about — so the local copy was re-added on
 *     top of the server's copy, causing visible duplication once the
 *     turn completed. We now track pending messages in a `pending` set
 *     keyed by content fingerprint, and `refreshActive` filters them
 *     out when the server has a matching (content+role) message.
 */

// Stable hash of a message's "who/when/what" so we can recognise when
// the server has confirmed an optimistic insert. We use this rather
// than the temp id because the server assigns a real UUID, so the id
// will never match.
function fingerprint(msg) {
  if (!msg) return '';
  return `${msg.role}|${msg.content || ''}`;
}
export const useChatStore = create((set, get) => ({
  conversations: [],
  active: null,
  messages: [],
  // Set of message fingerprints that were added optimistically and have
  // not yet been confirmed by the server. `refreshActive` filters
  // matching local messages out once the server has them.
  pending: new Set(),
  loading: false,
  error: null,
  startNewInFlight: false,

  async loadConversations() {
    set({ loading: true, error: null });
    try {
      const items = await apiClient.get('/chat/conversations');
      set({ conversations: items, loading: false });
    } catch (e) {
      set({ error: e.message, loading: false });
    }
  },

  async openConversation(id) {
    if (!id) return;
    // No-op if we're already on this conversation and have messages —
    // avoids re-fetching (and the resulting flash of empty state).
    if (get().active === id && get().messages.length > 0) return;
    // Clear messages synchronously on conversation change so the
    // previous conversation's messages don't bleed into the new one
    // while the fetch is in flight. The empty state is rendered for
    // the brief moment between this set and the fetch resolving.
    set({ active: id, messages: [], pending: new Set(), loading: true, error: null });
    try {
      const items = await apiClient.get(`/chat/conversations/${id}`);
      set({ messages: items || [], loading: false });
    } catch (e) {
      set({ error: e.message, loading: false });
    }
  },

  async startNew(firstMessage) {
    if (get().startNewInFlight) {
      // Return the in-flight id if we can, else null.
      return get().active;
    }
    set({ startNewInFlight: true });
    try {
      // Name the conversation from the user's first query — first 100
      // chars, trimmed — so the sidebar shows something meaningful
      // instead of the generic "New conversation". This runs at send
      // time (onSend passes the user's text), so the title is set on
      // the create call; the server's stream_turn then sees an
      // existing conversation_id and leaves the title untouched.
      // Falls back to "New conversation" when there's no seed
      // (defensive — startNew is currently only called from onSend,
      // which always has text). Matches the server's 100-char cap in
      // agent._ensure_conversation for the non-UI creation path.
      const seed = (firstMessage || '').trim().slice(0, 100);
      const conv = await apiClient.post('/chat/conversations', {
        title: seed || 'New conversation',
      });
      // De-dupe in case the user clicked twice before the first
      // response came back.
      const existing = get().conversations.find((c) => c.id === conv.id);
      const conversations = existing
        ? get().conversations
        : [conv, ...get().conversations];
      set({
        active: conv.id,
        messages: [],
        conversations,
        startNewInFlight: false,
      });
      return conv.id;
    } catch (e) {
      set({ error: e.message, startNewInFlight: false });
      return null;
    }
  },

  async deleteConversation(id) {
    try {
      await apiClient.del(`/chat/conversations/${id}`);
      const remaining = get().conversations.filter((c) => c.id !== id);
      set({ conversations: remaining });
      if (get().active === id) {
        set({ active: null, messages: [], pending: new Set() });
      }
    } catch (e) {
      set({ error: e.message });
      // Re-throw so the caller (ChatInterface) can toast the failure
      // instead of silently leaving the row in place.
      throw e;
    }
  },

  async renameConversation(id, title) {
    // Trim + cap client-side to match the schema (the server rejects
    // empty-after-strip with a 422, but we avoid the round-trip for an
    // obviously-empty rename). On success, patch the title (and the
    // server-bumped updated_at) into the list in place — we do NOT
    // reorder, so the row doesn't jump out from under the user mid-
    // edit; the next loadConversations will reflect the new order.
    const clean = (title || '').trim().slice(0, 100);
    if (!clean) return;
    try {
      const updated = await apiClient.patch(`/chat/conversations/${id}`, {
        title: clean,
      });
      set({
        conversations: get().conversations.map((c) =>
          c.id === id
            ? { ...c, title: updated.title, updated_at: updated.updated_at }
            : c,
        ),
      });
    } catch (e) {
      set({ error: e.message });
      // Re-throw so the caller (ChatInterface) can toast the failure —
      // the input has already closed, so without a toast a failed rename
      // silently reverts the row to the old title with zero feedback.
      throw e;
    }
  },

  appendMessage(msg) {
    if (!msg) return;
    const list = get().messages;
    // Dedupe by id (the streaming-finish path can fire twice).
    if (msg.id && list.some((m) => m.id === msg.id)) return;
    // NOTE: we intentionally do NOT dedupe by content fingerprint against the
    // full list here. A global `role|content` check silently dropped a second
    // genuinely-distinct send whose text happened to match an earlier message
    // (e.g. "hello" twice) — the optimistic append vanished until the server
    // round-trip resolved. Rapid double-clicks are already blocked by the
    // sendingRef/stream.runId guard in ChatInterface and by the id check
    // above; the pending-set logic in refreshActive is the only dedup needed
    // to collapse a local copy once the server confirms it.
    const fp = fingerprint(msg);
    const nextPending = new Set(get().pending);
    if (fp) nextPending.add(fp);
    set({ messages: [...list, msg], pending: nextPending });
  },

  setActive(id) {
    set({ active: id });
  },

  /**
   * Reset the active conversation view without a server call. Used when the
   * user starts a new chat ("+ New chat") or lands on /chat with no
   * conversation id: the URL is /chat but the store still held the previous
   * conversation's id + messages, so the old transcript stayed on screen and
   * new sends were persisted into the old conversation. Clearing here makes
   * the empty-state render and forces onSend down the startNew() path.
   */
  clearActive() {
    set({ active: null, messages: [], pending: new Set() });
  },

  /**
   * Refresh messages for the active conversation. The server's list is
   * authoritative: any local message whose fingerprint matches a
   * server message is dropped (the server has confirmed it), and any
   * remaining pending local messages are kept in their original order
   * (so the user's optimistic insert doesn't disappear during the
   * round-trip).
   */
  async refreshActive() {
    const id = get().active;
    if (!id) return;
    try {
      const items = await apiClient.get(`/chat/conversations/${id}`);
      const serverFps = new Set((items || []).map(fingerprint));
      const local = get().messages;
      // Drop local messages whose fingerprint is now on the server —
      // the server's version (with the real id, citations, etc.)
      // supersedes ours.
      const remainingLocal = local.filter((m) => {
        const fp = fingerprint(m);
        // If this local message was never marked pending, just keep
        // it as-is (defensive — should not happen in normal flow).
        if (!fp) return true;
        if (!get().pending.has(fp)) return true;
        return !serverFps.has(fp);
      });
      // Build the final list: server messages first (in server order),
      // then any pending locals that haven't been confirmed yet (in
      // their original relative order).
      const merged = [];
      const seen = new Set();
      for (const m of items || []) {
        merged.push(m);
        seen.add(fingerprint(m));
      }
      for (const m of remainingLocal) {
        const fp = fingerprint(m);
        if (fp && seen.has(fp)) continue;
        merged.push(m);
        if (fp) seen.add(fp);
      }
      // Anything on the server is no longer pending.
      const nextPending = new Set(get().pending);
      for (const fp of serverFps) nextPending.delete(fp);
      set({ messages: merged, pending: nextPending });
    } catch (e) {
      set({ error: e.message });
    }
  },
}));

export default useChatStore;

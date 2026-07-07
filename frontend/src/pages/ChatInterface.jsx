import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import { useChatStream } from '../hooks/useChatStream.js';
import { useChatStore } from '../store/chatStore.js';
import useConnectorsStore from '../store/connectorsStore.js';
import ModelPicker from '../components/ModelPicker.jsx';

/**
 * A stable, locally-unique temporary id generator. We avoid `Date.now()`
 * because two messages appended in the same tick (e.g. user + assistant)
 * would collide, breaking the React `key` and the dedup in chatStore.
 */
function tempId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `tmp-${crypto.randomUUID()}`;
  }
  return `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function ChatInterface() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const { conversationId } = useParams();

  const {
    conversations,
    messages,
    active,
    loadConversations,
    openConversation,
    startNew,
    deleteConversation,
    appendMessage,
    refreshActive,
    clearActive,
  } = useChatStore();

  const stream = useChatStream();
  // `stream` is a fresh object every render, but `reset`/`cancel` are stable
  // useCallback fns. Capture them by reference so effects depending on them
  // don't re-run on every render (which would, e.g., abort an in-flight POST).
  const { reset: streamReset, cancel: streamCancel } = stream;
  // Capture cancel in a ref so the unmount-cleanup effect has a stable
  // identity. If we depended on `stream` directly, the hook returns a
  // new object on every render, the effect would re-run on every
  // state change (including during a stream), and its cleanup would
  // abort the in-flight POST — producing "No data found for the
  // resource" in DevTools.
  const cancelRef = useRef(null);
  useEffect(() => {
    cancelRef.current = streamCancel;
  }, [streamCancel]);

  // Phase D: the chat UI reads from the connectors store to know
  // which `(connectorId, model)` to send in the chat request body.
  // The store is also persisted (activeModel is in localStorage) so
  // the picker's choice survives a page reload.
  const { activeModel } = useConnectorsStore();

  const [input, setInput] = useState('');
  const composerRef = useRef(null);
  const messagesEndRef = useRef(null);
  // Lock so the Send button can't queue a second click before the
  // first stream even hits RUN_STARTED (when runId is still null).
  const sendingRef = useRef(false);

  // Initial load + open the URL's conversation (if any).
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    if (conversationId) {
      openConversation(conversationId);
    } else {
      // On /chat with no conversation id, drop any leftover active
      // conversation + transcript. Without this, "+ New chat" (and a direct
      // nav to /chat) left the previous conversation's id/messages in the
      // store: the old transcript stayed on screen and new sends were
      // persisted into the old conversation. stream.reset() also clears any
      // leftover stream bubble / error from the prior turn.
      streamReset();
      clearActive();
    }
  }, [conversationId, openConversation, clearActive, streamReset]);

  // Cancel any in-flight stream on unmount so we don't leak sockets.
  // Depends on the stable ref, not on `stream`, so re-renders during a
  // run don't abort it.
  useEffect(() => () => cancelRef.current?.(), []);

  // Auto-scroll to the bottom when new messages arrive.
  useEffect(() => {
    const el = messagesEndRef.current;
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages.length, stream.text, stream.runId]);

  async function onSend() {
    const text = input.trim();
    // Operator-precedence fix: `!stream.done === false` was `stream.done`
    // — i.e. the guard was always-true. The real check is "is a run
    // already in flight?".
    if (!text) return;
    if (sendingRef.current || stream.runId) return;

    // Make sure we have a conversation to attach the message to.
    const wasNew = !active;
    let convId = active;
    if (!convId) convId = await startNew();
    if (!convId) return;

    // Sync the URL when a brand-new conversation was just created from the
    // /chat empty state. Without this the URL stayed /chat, so a refresh
    // dropped the active conversation (only reachable via the sidebar).
    // replace:true avoids leaving the bare /chat entry in history.
    if (wasNew) nav(`/chat/${convId}`, { replace: true });

    sendingRef.current = true;
    const userTempId = tempId();

    // Optimistically render the user message. The store tracks this as
    // pending so that, when the server confirms, `refreshActive` will
    // drop the local copy and replace it with the real one (no dup).
    appendMessage({
      id: userTempId,
      seq: 0,
      role: 'user',
      content: text,
      citations: [],
      used_tools: [],
      created_at: new Date().toISOString(),
    });

    setInput('');
    composerRef.current?.focus();

    let sentOk = true;
    try {
      await stream.send(text, {
        conversationId: convId,
        connectorId: activeModel?.connectorId || null,
        model: activeModel?.model || null,
      });
    } catch (e) {
      sentOk = false;
    } finally {
      sendingRef.current = false;
    }

    // The stream hook's `finally` clears `runId` before this await resolves,
    // which unmounts the stream bubble. The assistant text lived only in
    // `stream.text`, so without carrying it into `messages` the user saw a
    // flash of no-assistant-reply during the refreshActive round-trip.
    // Append the streamed turn optimistically; refreshActive's fingerprint
    // dedup drops this local copy once the server returns the real message.
    if (sentOk && stream.text) {
      appendMessage({
        id: tempId(),
        seq: 0,
        role: 'assistant',
        content: stream.text,
        citations: stream.citations,
        used_tools: stream.usedTools,
        created_at: new Date().toISOString(),
      });
    }

    // Reconcile with the server. The user's local message (pending) is
    // dropped because the server has the same content+role now; the
    // optimistic assistant copy is dropped the same way once the server's
    // version (with the real id, citations, used_tools) arrives.
    if (sentOk) {
      await refreshActive();
    }
    loadConversations();
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  }

  function onNewChat() {
    if (sendingRef.current || stream.runId) return;
    // If we're not on a conversation, do nothing — the empty state
    // already invites the user to type. Avoids creating an empty
    // conversation on every accidental click.
    if (active) {
      nav('/chat');
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h3 style={{ marginTop: 0 }}>Athena</h3>
        <p style={{ color: 'var(--text-dim)', fontSize: 12 }}>{user?.email}</p>
        <hr style={{ borderColor: 'var(--border)' }} />
        <button onClick={onNewChat}>+ New chat</button>
        <ul style={{ listStyle: 'none', padding: 0, marginTop: 16 }}>
          {conversations.map((c) => (
            <li
              key={c.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '4px 0',
              }}
            >
              <Link
                to={`/chat/${c.id}`}
                style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}
              >
                {c.title || `Conversation ${c.id.slice(0, 6)}`}
              </Link>
              <button
                className="secondary"
                style={{ padding: '2px 6px', fontSize: 12 }}
                onClick={async () => {
                  const wasActive = c.id === active;
                  await deleteConversation(c.id);
                  // deleteConversation resets active/messages when the active
                  // conv is deleted, but never updates the URL — so it left a
                  // stale /chat/:deletedId that 404'd on refresh/back-nav and
                  // rendered a blank panel. Navigate to /chat to match the
                  // now-empty store.
                  if (wasActive) nav('/chat', { replace: true });
                }}
                aria-label={`Delete ${c.title || 'conversation'}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        <hr style={{ borderColor: 'var(--border)' }} />
        <ul style={{ listStyle: 'none', padding: 0 }}>
          <li><Link to="/">📄 Documents</Link></li>
          <li><Link to="/connectors">🤖 Models</Link></li>
        </ul>
        <hr style={{ borderColor: 'var(--border)' }} />
        <button
          className="secondary"
          onClick={() => {
            logout();
            nav('/login');
          }}
        >
          Sign out
        </button>
      </aside>
      <main className="main">
        <header className="topbar">
          <h2 style={{ margin: 0 }}>Chat</h2>
          <ModelPicker />
        </header>
        <div className="chat">
          <div className="messages">
            {messages.map((m, i) => (
              <Bubble key={m.id || i} message={m} />
            ))}
            {stream.runId && (
              <>
                {stream.text === '' && !stream.done && (
                  <div
                    className="bubble assistant"
                    style={{ color: 'var(--text-dim)' }}
                  >
                    Thinking…
                  </div>
                )}
                {stream.text !== '' && (
                  <div className="bubble assistant">
                    {stream.text}
                    {!stream.done && <span style={{ opacity: 0.6 }}>▍</span>}
                  </div>
                )}
                {stream.toolEvents.length > 0 && (
                  <div className="bubble tool">
                    Tools:{' '}
                    {stream.toolEvents
                      .map((t) => `${t.name} (${t.status})`)
                      .join(', ')}
                  </div>
                )}
              </>
            )}
            {/* Error bubble is hoisted OUT of the runId guard: failures that
                happen before RUN_STARTED (network drop, 401, 4xx/5xx on the
                POST, abort before the reader loop) leave runId null, so
                nesting it inside the guard rendered nothing and the app
                appeared to hang silently. */}
            {stream.error && (
              <div
                className="bubble"
                style={{ borderColor: 'var(--danger)' }}
                role="alert"
              >
                Error: {stream.error}
              </div>
            )}
            {!active && messages.length === 0 && !stream.runId && (
              <div
                className="bubble"
                style={{ alignSelf: 'center', color: 'var(--text-dim)' }}
              >
                Start a new conversation to begin.
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          <div className="composer">
            <textarea
              ref={composerRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about your documents…"
              onKeyDown={onKeyDown}
              rows={1}
              aria-label="Message"
            />
            <button
              onClick={onSend}
              disabled={sendingRef.current || stream.runId != null}
            >
              {stream.runId ? '…' : 'Send'}
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

function Bubble({ message }) {
  return (
    <div className={`bubble ${message.role}`}>
      {message.content}
      {message.citations && message.citations.length > 0 && (
        <div className="citation">
          Citations:{' '}
          {message.citations
            .map(
              (c) =>
                `[chunk:${(c.chunk_id || '').slice(0, 8)}] ${
                  c.document_name || ''
                }${c.page_number != null ? ` p.${c.page_number}` : ''}`,
            )
            .join(' · ')}
        </div>
      )}
    </div>
  );
}

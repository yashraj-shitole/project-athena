import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import { useChatStream } from '../hooks/useChatStream.js';
import { useChatStore } from '../store/chatStore.js';

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
  } = useChatStore();

  const stream = useChatStream();
  // Capture cancel in a ref so the unmount-cleanup effect has a stable
  // identity. If we depended on `stream` directly, the hook returns a
  // new object on every render, the effect would re-run on every
  // state change (including during a stream), and its cleanup would
  // abort the in-flight POST — producing "No data found for the
  // resource" in DevTools.
  const cancelRef = useRef(null);
  useEffect(() => {
    cancelRef.current = stream.cancel;
  }, [stream.cancel]);

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
    }
  }, [conversationId, openConversation]);

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
    let convId = active;
    if (!convId) convId = await startNew();
    if (!convId) return;

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
      await stream.send(text, { conversationId: convId });
    } catch (e) {
      sentOk = false;
    } finally {
      sendingRef.current = false;
    }

    // Reconcile with the server. The user's local message (pending) is
    // dropped because the server has the same content+role now. The
    // assistant message was never appended locally — the stream-bubble
    // showed it during the run and `refreshActive` pulls the real
    // (with citations/used_tools) one from the server.
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
                onClick={() => deleteConversation(c.id)}
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
                {stream.error && (
                  <div
                    className="bubble"
                    style={{ borderColor: 'var(--danger)' }}
                    role="alert"
                  >
                    Error: {stream.error}
                  </div>
                )}
              </>
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

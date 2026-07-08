import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Sparkles, Plus, MessageSquare, Trash2, Send, Square,
  ArrowUp, AlertCircle, Wrench, MoreHorizontal, FileText,
  Brain, LogOut, Pencil,
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth.js';
import { useChatStream } from '../hooks/useChatStream.js';
import { useChatStore } from '../store/chatStore.js';
import useConnectorsStore from '../store/connectorsStore.js';
import ModelPicker from '../components/ModelPicker.jsx';
import AppShell from '../components/ui/AppShell.jsx';
import Avatar from '../components/ui/Avatar.jsx';
import Button from '../components/ui/Button.jsx';
import Textarea from '../components/ui/Textarea.jsx';
import { Tooltip } from '../components/ui/Tooltip.jsx';
import {
  DropdownMenu, DropdownItem, DropdownSeparator,
} from '../components/ui/DropdownMenu.jsx';
import Markdown from '../components/ui/Markdown.jsx';
import { fadeUp, pageEnter } from '../components/ui/Motion.jsx';
import { useToast } from '../components/ui/Toaster.jsx';
import { cn } from '../lib/cn.js';

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

const SUGGESTIONS = [
  'Summarize the key points from my most recent document.',
  'What questions does this material answer?',
  'Compare two documents side by side.',
  'Find passages that mention a specific topic.',
];

export default function ChatInterface() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const { conversationId } = useParams();
  const toast = useToast();

  const {
    conversations,
    messages,
    active,
    loadConversations,
    openConversation,
    startNew,
    deleteConversation,
    renameConversation,
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

  // Auto-resize the composer textarea up to a cap so it grows with
  // multi-line input without becoming the whole screen.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  async function onSend(textOverride) {
    const text = (textOverride ?? input).trim();
    // Operator-precedence fix: `!stream.done === false` was `stream.done`
    // — i.e. the guard was always-true. The real check is "is a run
    // already in flight?".
    if (!text) return;
    if (sendingRef.current || stream.runId) return;

    // Make sure we have a conversation to attach the message to.
    const wasNew = !active;
    let convId = active;
    // Pass the user's text so startNew can name the conversation from
    // the first query (first 100 chars) instead of "New conversation".
    if (!convId) convId = await startNew(text);
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

    if (textOverride == null) {
      setInput('');
      composerRef.current?.focus();
    }

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
    <AppShell
      sidebar={
        <ChatSidebar
          user={user}
          onSignOut={() => { logout(); nav('/login'); }}
          conversations={conversations}
          active={active}
          onPick={(c) => nav(`/chat/${c.id}`)}
          onRename={async (c, title) => {
            try {
              await renameConversation(c.id, title);
            } catch (e) {
              toast.show(e.message || 'Could not rename conversation.', { tone: 'error' });
            }
          }}
          onDelete={async (c) => {
            const wasActive = c.id === active;
            try {
              await deleteConversation(c.id);
              // deleteConversation resets active/messages when the active
              // conv is deleted, but never updates the URL — so it left a
              // stale /chat/:deletedId that 404'd on refresh/back-nav and
              // rendered a blank panel. Navigate to /chat to match the
              // now-empty store. Only navigate on a successful delete.
              if (wasActive) nav('/chat', { replace: true });
            } catch (e) {
              toast.show(e.message || 'Could not delete conversation.', { tone: 'error' });
            }
          }}
        />
      }
    >
      <header className="flex h-14 items-center justify-between gap-3 px-6 border-b border-hairline bg-surface/40 backdrop-blur-sm shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <Button variant="ghost" onClick={onNewChat}>
            <Plus size={14} strokeWidth={1.75} />
            New chat
          </Button>
        </div>
        <div className="flex items-center gap-3 min-w-0">
          <ModelPicker />
        </div>
      </header>

      <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
        <div className="flex-1 min-h-0 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-6 py-8 flex flex-col gap-6">
            <AnimatePresence mode="wait">
              {messages.length === 0 && !stream.runId ? (
                <EmptyState key="empty" onPick={onSend} />
              ) : (
                <motion.div
                  key="messages"
                  variants={pageEnter}
                  initial="hidden"
                  animate="show"
                  className="flex flex-col gap-4"
                >
                  {messages.map((m, i) => (
                    <Bubble key={m.id || i} message={m} />
                  ))}
                  {stream.runId && (
                    <>
                      {stream.text === '' && !stream.done && (
                        <Bubble
                          message={{
                            role: 'assistant',
                            content: 'Thinking…',
                            isPending: true,
                          }}
                        />
                      )}
                      {stream.text !== '' && (
                        <Bubble
                          message={{
                            role: 'assistant',
                            content: stream.text,
                            streaming: !stream.done,
                          }}
                        />
                      )}
                      {stream.toolEvents.length > 0 && (
                        <ToolCallRow events={stream.toolEvents} />
                      )}
                    </>
                  )}
                  {stream.error && (
                    <div
                      className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3.5 py-2.5 text-sm text-[var(--danger)] flex items-start gap-2 self-start max-w-[80%]"
                      role="alert"
                    >
                      <AlertCircle size={16} strokeWidth={1.75} className="mt-0.5 shrink-0" />
                      <span>Error: {stream.error}</span>
                    </div>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
            <div ref={messagesEndRef} />
          </div>
        </div>

        <div className="shrink-0 border-t border-hairline bg-surface/60 backdrop-blur-sm">
          <div className="mx-auto w-full max-w-3xl px-6 py-4">
            <Composer
              ref={composerRef}
              value={input}
              onChange={setInput}
              onKeyDown={onKeyDown}
              onSend={() => onSend()}
              onStop={() => streamCancel()}
              busy={!!stream.runId}
            />
            <p className="mt-2 text-center text-[11px] text-ink-faint">
              Athena can make mistakes. Verify important answers against the source documents.
            </p>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function ChatSidebar({ user, onSignOut, conversations, active, onPick, onRename, onDelete }) {
  // id of the conversation whose title is being inline-edited, or null.
  const [editingId, setEditingId] = useState(null);
  // id of the conversation whose row should reclaim keyboard focus once
  // the inline editor unmounts (after commit or cancel). Without this,
  // setEditingId(null) unmounts the focused <input> and focus falls to
  // <body>, stranding keyboard users. The matching <Link> focuses itself
  // via a ref callback when it remounts, then clears this id.
  const [focusRestoreId, setFocusRestoreId] = useState(null);

  async function commitRename(c, newTitle) {
    // Clear the editing state first so the input unmounts and its
    // blur can't re-fire commit (RenameInput also guards with its own
    // committedRef, but this keeps the parent side single-source).
    setFocusRestoreId(c.id);
    setEditingId(null);
    await onRename?.(c, newTitle);
  }
  function cancelRename(c) {
    setFocusRestoreId(c.id);
    setEditingId(null);
  }

  return (
    <aside className="flex flex-col h-full w-[260px] shrink-0 border-r border-hairline bg-surface">
      <div className="px-5 py-5">
        <Link to="/" className="flex items-center gap-2 group">
          <Logo />
          <span className="text-base font-medium tracking-tight text-ink">Athena</span>
        </Link>
      </div>

      <div className="px-3 pb-2">
        <p className="px-2 text-[10px] font-medium uppercase tracking-wider text-ink-faint">
          Conversations
        </p>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-3">
        {conversations.length === 0 ? (
          <p className="px-2 text-xs text-ink-faint italic">
            Start a new conversation to see it here.
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {conversations.map((c) => {
              const isActive = c.id === active;
              const isEditing = editingId === c.id;
              const label = c.title || `Conversation ${c.id.slice(0, 6)}`;
              return (
                <li key={c.id} className="group/conv relative">
                  {isEditing ? (
                    // Inline edit: a plain div (not a form) so Enter in
                    // the input is handled by RenameInput and never
                    // double-fires a form submit. The input fills the
                    // row; the action buttons are hidden while editing.
                    // The edit row mirrors the row's active/inactive
                    // styling so editing an inactive conversation
                    // doesn't suddenly look active (bg-surface-2 is the
                    // active-row fill).
                    <div
                      className={cn(
                        'flex items-center gap-2 rounded-md pl-2.5 pr-2 py-1.5 text-sm',
                        isActive
                          ? 'bg-surface-2 text-ink font-medium'
                          : 'text-ink-dim',
                      )}
                    >
                      <MessageSquare size={13} strokeWidth={1.75} className="shrink-0 text-ink-faint" />
                      <RenameInput
                        initial={label}
                        textClassName={isActive ? 'text-ink font-medium' : 'text-ink-dim'}
                        onCommit={(v) => commitRename(c, v)}
                        onCancel={() => cancelRename(c)}
                      />
                    </div>
                  ) : (
                    <>
                      <Link
                        to={`/chat/${c.id}`}
                        // Reclaim focus here after the inline editor
                        // closes (commit/cancel set focusRestoreId). The
                        // ref callback fires when this Link remounts on
                        // the editing->idle transition; if the id matches
                        // we focus it and clear the request.
                        ref={(el) => {
                          if (el && focusRestoreId === c.id) {
                            el.focus();
                            setFocusRestoreId(null);
                          }
                        }}
                        className={cn(
                          'flex items-center gap-2 rounded-md pl-2.5 pr-14 py-1.5 text-sm',
                          'transition-colors duration-[var(--motion-fast)]',
                          isActive
                            ? 'bg-surface-2 text-ink font-medium'
                            : 'text-ink-dim hover:bg-surface-2/60 hover:text-ink',
                        )}
                      >
                        <MessageSquare size={13} strokeWidth={1.75} className="shrink-0 text-ink-faint" />
                        <span className="truncate flex-1">
                          {label}
                        </span>
                      </Link>
                      {/* Rename — sits to the left of delete. Both are
                          absolute-positioned siblings of the Link so
                          clicking them doesn't navigate.
                          Reveal: hover (desktop mouse), focus-within
                          (sighted keyboard users tabbing to the row),
                          and @media(hover:none) (touch — the buttons are
                          otherwise unreachable on touch devices). Until
                          revealed they are pointer-events-none so the
                          invisible buttons don't intercept clicks on the
                          right edge of the row (which would trigger
                          rename/delete instead of navigating). The
                          Link's pr-14 reserves this 56px strip so long
                          titles ellipsize before the buttons. */}
                      <button
                        className={cn(
                          'absolute right-8 top-1/2 -translate-y-1/2 inline-flex h-6 w-6 items-center justify-center rounded-md',
                          'text-ink-faint hover:text-ink hover:bg-surface-2',
                          'opacity-0 transition-opacity',
                          'group-hover/conv:opacity-100 group-focus-within/conv:opacity-100 [@media(hover:none)]:opacity-100',
                          'pointer-events-none group-hover/conv:pointer-events-auto group-focus-within/conv:pointer-events-auto [@media(hover:none)]:pointer-events-auto',
                        )}
                        onClick={() => setEditingId(c.id)}
                        aria-label={`Rename ${label}`}
                      >
                        <Pencil size={12} strokeWidth={1.75} />
                      </button>
                      <button
                        className={cn(
                          'absolute right-1 top-1/2 -translate-y-1/2 inline-flex h-6 w-6 items-center justify-center rounded-md',
                          'text-ink-faint hover:text-[var(--danger)] hover:bg-[var(--danger-bg)]',
                          'opacity-0 transition-opacity',
                          'group-hover/conv:opacity-100 group-focus-within/conv:opacity-100 [@media(hover:none)]:opacity-100',
                          'pointer-events-none group-hover/conv:pointer-events-auto group-focus-within/conv:pointer-events-auto [@media(hover:none)]:pointer-events-auto',
                        )}
                        onClick={() => onDelete(c)}
                        aria-label={`Delete ${label}`}
                      >
                        <Trash2 size={12} strokeWidth={1.75} />
                      </button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <nav className="px-3 pb-3">
        <p className="px-2 mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-faint">
          Workspace
        </p>
        <ul className="flex flex-col gap-0.5">
          <li>
            <Link
              to="/"
              className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-ink-dim hover:bg-surface-2/60 hover:text-ink transition-colors"
            >
              <FileText size={14} strokeWidth={1.75} />
              Documents
            </Link>
          </li>
          <li>
            <Link
              to="/connectors"
              className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-ink-dim hover:bg-surface-2/60 hover:text-ink transition-colors"
            >
              <Brain size={14} strokeWidth={1.75} />
              Models
            </Link>
          </li>
        </ul>
      </nav>

      <div className="mt-auto px-3 pb-4 pt-3 border-t border-hairline">
        <div className="flex items-center gap-2.5 px-2 py-1.5">
          <Avatar name={user?.email || 'User'} size={28} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-ink">{user?.email || '—'}</p>
          </div>
          <Tooltip content="Sign out">
            <Button variant="ghost" size="icon-sm" onClick={onSignOut} aria-label="Sign out">
              <LogOut size={14} strokeWidth={1.75} />
            </Button>
          </Tooltip>
        </div>
      </div>
    </aside>
  );
}

function Logo() {
  return (
    <span
      className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-[var(--accent)] text-[var(--accent-fg)]"
      aria-hidden
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        <path d="M7 1L11.5 4V10L7 13L2.5 10V4L7 1Z" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" />
        <circle cx="7" cy="7" r="1.6" fill="currentColor" />
      </svg>
    </span>
  );
}

function EmptyState({ onPick }) {
  return (
    <motion.div
      key="empty"
      variants={fadeUp}
      initial="hidden"
      animate="show"
      className="flex flex-col items-center text-center pt-12 pb-4"
    >
      <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--accent)] text-[var(--accent-fg)] mb-5">
        <Sparkles size={22} strokeWidth={1.5} />
      </div>
      <h2 className="text-display-2 font-medium tracking-tight text-ink">
        What can I help you find?
      </h2>
      <p className="mt-2 max-w-md text-sm text-ink-dim leading-relaxed">
        Ask anything about your documents. Athena grounds every answer in the
        source chunks and shows you exactly where it found them.
      </p>
      <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-2xl">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => onPick(s)}
            className="text-left rounded-xl border border-hairline bg-surface px-4 py-3 text-sm text-ink-dim hover:border-hairline-strong hover:text-ink transition-colors"
          >
            {s}
          </button>
        ))}
      </div>
    </motion.div>
  );
}

/**
 * Inline rename field. Autofocuses + selects the current title on
 * mount; Enter or blur commits (rename), Escape cancels. A
 * `committedRef` guard stops the blur that fires when the input
 * unmounts after Enter from calling commit a second time.
 */
const RenameInput = React.forwardRef(function RenameInput(
  { initial, onCommit, onCancel, textClassName }, ref,
) {
  const [value, setValue] = useState(initial);
  const innerRef = useRef(null);
  const committedRef = useRef(false);

  useEffect(() => {
    const el = innerRef.current;
    if (el) {
      el.focus();
      el.select();
    }
    // Mount-only — we don't want to re-select on every keystroke.
  }, []);

  function commit() {
    if (committedRef.current) return;
    committedRef.current = true;
    const v = value.trim().slice(0, 100);
    if (v && v !== initial) onCommit(v);
    else onCancel();
  }

  return (
    <input
      ref={(node) => {
        innerRef.current = node;
        if (ref) ref.current = node;
      }}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          commit();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          if (!committedRef.current) {
            committedRef.current = true;
            onCancel();
          }
        }
      }}
      onBlur={commit}
      maxLength={100}
      aria-label="Conversation name"
      className={cn(
        'flex-1 min-w-0 bg-transparent text-sm outline-none border-0 p-0',
        textClassName || 'text-ink',
      )}
    />
  );
});

const Composer = React.forwardRef(function Composer(
  { value, onChange, onKeyDown, onSend, onStop, busy }, ref,
) {
  return (
    <div className="rounded-2xl border border-hairline bg-surface shadow-soft p-2 flex items-end gap-2 transition-colors focus-within:border-hairline-strong">
      <Textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask anything about your documents…"
        rows={1}
        aria-label="Message"
        className="border-0 bg-transparent px-2 py-2.5 shadow-none focus:border-0 hover:border-0 min-h-[40px]"
        style={{ resize: 'none' }}
      />
      {busy ? (
        <Tooltip content="Stop generating">
          <Button
            variant="secondary"
            size="icon"
            onClick={onStop}
            aria-label="Stop generating"
            className="shrink-0"
          >
            <Square size={14} strokeWidth={1.75} fill="currentColor" />
          </Button>
        </Tooltip>
      ) : (
        <Tooltip content="Send (Enter)">
          <Button
            variant="primary"
            size="icon"
            onClick={onSend}
            disabled={!value.trim()}
            aria-label="Send"
            className="shrink-0"
          >
            <ArrowUp size={16} strokeWidth={2} />
          </Button>
        </Tooltip>
      )}
    </div>
  );
});

function Bubble({ message }) {
  const isUser = message.role === 'user';
  const isPending = message.isPending;
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        'flex flex-col gap-1.5',
        isUser ? 'items-end' : 'items-start',
      )}
    >
      <div
        className={cn(
          'max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed',
          isUser
            ? 'bg-[var(--accent)] text-[var(--accent-fg)] rounded-br-md'
            : 'bg-surface border border-hairline text-ink rounded-bl-md',
        )}
      >
        {isPending ? (
          <span className="text-ink-dim italic">{message.content}</span>
        ) : isUser ? (
          <span className="whitespace-pre-wrap">{message.content}</span>
        ) : (
          <Markdown>{message.content}</Markdown>
        )}
        {message.streaming && (
          <span
            className="ml-0.5 inline-block h-4 w-[2px] -mb-0.5 align-middle bg-current animate-caret-blink"
            aria-hidden
          />
        )}
      </div>
      {message.citations && message.citations.length > 0 && (
        <div className="max-w-[80%] flex flex-wrap gap-1.5">
          {message.citations.map((c, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 rounded-full border border-hairline bg-surface px-2 py-0.5 text-[10px] text-ink-dim"
              title={c.document_name || ''}
            >
              <span className="font-mono">[{c.chunk_id?.slice(0, 8) || '—'}]</span>
              <span className="truncate max-w-[180px]">{c.document_name}</span>
              {c.page_number != null && <span>p.{c.page_number}</span>}
            </span>
          ))}
        </div>
      )}
    </motion.div>
  );
}

function ToolCallRow({ events }) {
  return (
    <div className="rounded-lg border border-hairline bg-surface-2/40 px-3 py-2 text-xs text-ink-dim flex items-center gap-2 self-start max-w-[80%]">
      <Wrench size={12} strokeWidth={1.75} />
      <span>
        Tools: {events.map((t) => `${t.name} (${t.status})`).join(', ')}
      </span>
    </div>
  );
}

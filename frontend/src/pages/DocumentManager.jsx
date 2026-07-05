import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import docService from '../services/docService.js';

// Polling cadence. We only poll fast when something is in motion.
const POLL_FAST_MS = 2000;
const POLL_SLOW_MS = 15000;

// Backend supports these extensions. Keep in sync with the
// FastAPI config (`ATHENA_UPLOAD_ALLOWED_TYPES`).
const ACCEPT = '.csv,.xlsx,.pdf,.doc,.docx,.txt,.md,.html,.htm';

export default function DocumentManager() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const fileRef = useRef(null);
  const [filter, setFilter] = useState('');

  // `loadRef` is the single source of truth for the latest fetcher,
  // so the polling interval (which is started once) always calls the
  // current version, not a stale closure from the first render.
  const loadRef = useRef(null);
  // `docsRef` is the latest docs snapshot — read by the polling loop
  // without forcing the effect to re-run on every `setDocs`.
  const docsRef = useRef([]);

  async function load() {
    try {
      const out = await docService.list({ limit: 100 });
      setDocs(out.items);
      setTotal(out.total);
      docsRef.current = out.items;
      setErr(null);
    } catch (e) {
      if (e.aborted) return;
      setErr(e.message);
    }
  }
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      if (cancelled) return;
      await loadRef.current?.();
      if (cancelled) return;
      // Pick cadence based on whether anything is in flight. Read
      // the latest docs via the ref so we don't depend on the
      // `docs` state (which would re-run this effect on every tick
      // and create an infinite polling loop).
      const anyBusy = docsRef.current.some(
        (d) => d.status === 'uploaded' || d.status === 'processing',
      );
      timer = setTimeout(tick, anyBusy ? POLL_FAST_MS : POLL_SLOW_MS);
    };

    // Kick off immediately, then self-schedule.
    tick();

    // Pause polling when the tab is hidden — saves battery + bandwidth.
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        // Fire a fresh load when the user comes back.
        loadRef.current?.();
      }
    };
    document.addEventListener('visibilitychange', onVis);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onPick(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setUploading(true);
    setErr(null);
    try {
      await docService.upload(f);
      e.target.value = '';
      // Force a refresh so the user sees the new row.
      await load();
    } catch (e2) {
      setErr(e2?.body?.detail || e2?.message || 'upload failed');
    } finally {
      setUploading(false);
    }
  }

  async function onConfirmDelete() {
    if (!pendingDelete) return;
    // `pendingDelete` is the full document object (set in the row's
    // Delete button). Pull out the id; otherwise the request goes to
    // `/api/documents/[object Object]` and 422s.
    const id = pendingDelete.id;
    setPendingDelete(null);
    try {
      await docService.remove(id);
      await load();
    } catch (e) {
      setErr(e.message);
    }
  }

  const visible = filter ? docs.filter((d) => d.status === filter) : docs;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h3 style={{ marginTop: 0 }}>Athena</h3>
        <p style={{ color: 'var(--text-dim)', fontSize: 12 }}>{user?.email}</p>
        <hr style={{ borderColor: 'var(--border)' }} />
        <ul style={{ listStyle: 'none', padding: 0 }}>
          <li>
            <Link to="/">📄 Documents</Link>
          </li>
          <li>
            <Link to="/chat">💬 Chat</Link>
          </li>
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
          <h2 style={{ margin: 0 }}>Documents</h2>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              aria-label="Filter by status"
            >
              <option value="">All</option>
              <option value="uploaded">Uploaded</option>
              <option value="processing">Processing</option>
              <option value="indexed">Indexed</option>
              <option value="failed">Failed</option>
            </select>
            <button
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? 'Uploading…' : 'Upload'}
            </button>
            <input
              ref={fileRef}
              type="file"
              hidden
              accept={ACCEPT}
              onChange={onPick}
            />
          </div>
        </header>
        <div className="content">
          {err && (
            <div
              className="card"
              style={{ borderColor: 'var(--danger)' }}
              role="alert"
            >
              {err}
            </div>
          )}
          <p style={{ color: 'var(--text-dim)' }}>
            {total} document{total === 1 ? '' : 's'}.
          </p>
          <div className="docs-list">
            {visible.map((d) => (
              <div className="doc-row" key={d.id}>
                <div>
                  <div style={{ fontWeight: 500 }}>{d.filename}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                    {d.file_type} · {(d.size_bytes / 1024).toFixed(1)} KB ·{' '}
                    {d.page_count ? `${d.page_count} pages · ` : ''}
                    {new Date(d.created_at).toLocaleString()}
                    {d.error_message ? ` · error: ${d.error_message}` : ''}
                  </div>
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                  }}
                >
                  <span className={`status-pill status-${d.status}`}>
                    {d.status}
                  </span>
                  <button
                    className="danger"
                    onClick={() => setPendingDelete(d)}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
            {visible.length === 0 && (
              <div
                className="card"
                style={{ textAlign: 'center', color: 'var(--text-dim)' }}
              >
                No documents yet. Upload a CSV/XLSX/PDF/DOC/DOCX/TXT/MD/HTML
                to get started.
              </div>
            )}
          </div>
        </div>
      </main>

      {pendingDelete && (
        <div
          className="modal-backdrop"
          onClick={() => setPendingDelete(null)}
          role="presentation"
        >
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <h3>Delete document?</h3>
            <p>
              <strong>{pendingDelete.filename}</strong> and all of its
              indexed chunks will be permanently removed.
            </p>
            <div className="actions">
              <button
                className="secondary"
                onClick={() => setPendingDelete(null)}
              >
                Cancel
              </button>
              <button className="danger" onClick={onConfirmDelete}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

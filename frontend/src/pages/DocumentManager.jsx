import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import docService from '../services/docService.js';
import DocumentCard from '../components/DocumentCard.jsx';

// Polling cadence. We only poll fast when something is in motion.
// (The per-doc SSE stream drives the in-card progress UI; this list-
// level poll keeps the row list in sync for new uploads and is the
// fallback if a user is on a different replica from the one running
// the pipeline.)
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
  const [deletingId, setDeletingId] = useState(null);
  const [loadedOnce, setLoadedOnce] = useState(false);
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
    } finally {
      // Mark first load complete on success OR failure so the initial
      // "No documents yet" flash (painted before the first fetch resolves)
      // is replaced by a neutral loading state instead of a misleading
      // empty-account message.
      setLoadedOnce(true);
    }
  }
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      if (cancelled) return;
      // Skip fetching while the tab is hidden — the line-68 comment promised
      // this savings but the old handler only re-fetched on becoming visible
      // and never stopped the self-scheduling loop, so polling continued in
      // background tabs. Re-schedule slowly and bail until visible again.
      if (!document.hidden) {
        await loadRef.current?.();
        if (cancelled) return;
      }
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

    // Fire a fresh load when the user comes back to the tab.
    const onVis = () => {
      if (document.visibilityState === 'visible') {
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
    // Reset the input value up front, on every path. Previously this ran only
    // inside `try` after a successful upload, so a failed upload left the
    // input holding the same file — and browsers don't fire `change` for an
    // unchanged value, so the user couldn't re-pick the same file to retry
    // without first choosing a different file or reloading.
    e.target.value = '';
    setUploading(true);
    setErr(null);
    try {
      await docService.upload(f);
      // Force a refresh so the user sees the new row.
      await load();
    } catch (e2) {
      // Prefer the already-flattened human-readable message. `e2.body.detail`
      // can be a FastAPI validation array of {loc,msg,...} objects, which
      // React cannot render ("Objects are not valid as a React child") — so
      // only fall back to it when it is a plain string.
      const detail = e2?.body?.detail;
      const msg =
        (typeof detail === 'string' ? detail : null) ||
        e2?.message ||
        'upload failed';
      setErr(msg);
    } finally {
      setUploading(false);
    }
  }

  async function onConfirmDelete() {
    if (!pendingDelete) return;
    // `pendingDelete` is the full document object (set in the row's
    // Delete button). Pull out the id; otherwise the request goes to
    // `/api/documents/[object Object]` and 422s.
    const doc = pendingDelete;
    setDeletingId(doc.id);
    try {
      await docService.remove(doc.id);
      // Close the modal only after the delete succeeds. Previously
      // setPendingDelete(null) ran before the await, so the in-flight
      // DELETE gave no feedback, the row's Delete button stayed clickable
      // (allowing a duplicate concurrent delete), and a failure surfaced
      // only as a generic banner with no indication of which file failed.
      setPendingDelete(null);
      await load();
    } catch (e) {
      setErr(`Could not delete ${doc.filename}: ${e.message}`);
    } finally {
      setDeletingId(null);
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
              <DocumentCard
                key={d.id}
                doc={d}
                onDelete={(docArg) => setPendingDelete(docArg)}
              />
            ))}
            {visible.length === 0 &&
              (loadedOnce ? (
                docs.length === 0 ? (
                  <div
                    className="card"
                    style={{ textAlign: 'center', color: 'var(--text-dim)' }}
                  >
                    No documents yet. Upload a
                    CSV/XLSX/PDF/DOC/DOCX/TXT/MD/HTML to get started.
                  </div>
                ) : (
                  <div
                    className="card"
                    style={{ textAlign: 'center', color: 'var(--text-dim)' }}
                  >
                    No documents match the “{filter}” filter.
                  </div>
                )
              ) : (
                <div
                  className="card"
                  style={{ textAlign: 'center', color: 'var(--text-dim)' }}
                >
                  Loading documents…
                </div>
              ))}
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
                disabled={!!deletingId}
              >
                Cancel
              </button>
              <button
                className="danger"
                onClick={onConfirmDelete}
                disabled={!!deletingId}
              >
                {deletingId ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

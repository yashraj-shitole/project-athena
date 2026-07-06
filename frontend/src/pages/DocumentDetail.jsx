import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import docService from '../services/docService.js';
import DocumentCard from '../components/DocumentCard.jsx';

/**
 * /documents/:id — the document detail page. Renders the same
 * DocumentCard as the list (so progress, retry, and the success
 * block behave identically), followed by the chunk list so the user
 * can see what was actually indexed.
 */
export default function DocumentDetail() {
  const { id } = useParams();
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [doc, setDoc] = useState(null);
  const [chunks, setChunks] = useState(null);
  const [err, setErr] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const d = await docService.get(id);
        if (!cancelled) setDoc(d);
        try {
          const c = await docService.chunks(id);
          if (!cancelled) setChunks(c);
        } catch (e2) {
          // Chunks failing isn't fatal — show the doc without them.
          if (!cancelled) setChunks([]);
        }
      } catch (e) {
        if (!cancelled) setErr(e.message || 'Failed to load document');
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleDelete = async () => {
    try {
      await docService.remove(id);
      nav('/');
    } catch (e) {
      setErr(e.message || 'Delete failed');
      setConfirmingDelete(false);
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <h3 style={{ marginTop: 0 }}>Athena</h3>
        <p style={{ color: 'var(--text-dim)', fontSize: 12 }}>{user?.email}</p>
        <hr style={{ borderColor: 'var(--border)' }} />
        <ul style={{ listStyle: 'none', padding: 0 }}>
          <li><Link to="/">📄 Documents</Link></li>
          <li><Link to="/chat">💬 Chat</Link></li>
        </ul>
        <hr style={{ borderColor: 'var(--border)' }} />
        <button
          className="secondary"
          onClick={() => { logout(); nav('/login'); }}
        >
          Sign out
        </button>
      </aside>
      <main className="main">
        <header className="topbar">
          <h2 style={{ margin: 0 }}>Document</h2>
        </header>
        <div className="content">
          {err && (
            <div className="card" style={{ borderColor: 'var(--danger)' }} role="alert">
              {err}
            </div>
          )}
          {!doc && !err && (
            <div className="card" style={{ color: 'var(--text-dim)' }}>
              Loading…
            </div>
          )}
          {doc && (
            <div className="detail-page">
              <Link to="/" className="detail-back">← Back to documents</Link>
              <DocumentCard
                doc={doc}
                showOpen={false}
                onDelete={() => setConfirmingDelete(true)}
              />
              <div className="detail-chunks">
                <h3>Indexed chunks</h3>
                {chunks == null ? (
                  <p style={{ color: 'var(--text-dim)' }}>Loading chunks…</p>
                ) : chunks.length === 0 ? (
                  <p style={{ color: 'var(--text-dim)' }}>
                    No chunks yet. Once indexing finishes, the chunk
                    contents will appear here.
                  </p>
                ) : (
                  chunks.map((c) => (
                    <div key={c.id} className="detail-chunk">
                      <div className="detail-chunk-meta">
                        #{c.chunk_index}
                        {c.page_number ? ` · page ${c.page_number}` : ''}
                      </div>
                      <div>{c.content}</div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {confirmingDelete && doc && (
        <div
          className="modal-backdrop"
          onClick={() => setConfirmingDelete(false)}
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
              <strong>{doc.filename}</strong> and all of its
              indexed chunks will be permanently removed.
            </p>
            <div className="actions">
              <button
                className="secondary"
                onClick={() => setConfirmingDelete(false)}
              >
                Cancel
              </button>
              <button className="danger" onClick={handleDelete}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

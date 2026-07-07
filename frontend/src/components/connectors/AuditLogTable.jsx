/**
 * Paginated audit log for a connector. The backend returns
 * `{rows, total, limit, offset}` — we walk it with a simple
 * "Load more" button rather than a full page UI.
 */
import React, { useEffect, useState } from 'react';
import connectorService from '../../services/connectorService.js';

const PAGE = 25;

function summarize(d) {
  if (!d) return '';
  try {
    return JSON.stringify(d);
  } catch {
    return String(d);
  }
}

export default function AuditLogTable({ connector }) {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [offset, setOffset] = useState(0);

  const load = async (reset = false) => {
    if (!connector?.id) return;
    setLoading(true);
    setError(null);
    try {
      const off = reset ? 0 : offset;
      const res = await connectorService.audit(connector.id, {
        limit: PAGE,
        offset: off,
      });
      const newRows = res?.rows || [];
      setRows(reset ? newRows : [...rows, ...newRows]);
      setTotal(res?.total || 0);
      setOffset(off + newRows.length);
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setRows([]);
    setOffset(0);
    setTotal(0);
    load(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connector?.id]);

  return (
    <div className="audit-log-table">
      {error && <div className="test-panel-error">⚠ {error}</div>}
      {!rows.length && !loading && (
        <div className="audit-empty">No audit events yet.</div>
      )}
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Action</th>
            <th>Before</th>
            <th>After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id || `${r.at}-${Math.random()}`}>
              <td>{r.at ? new Date(r.at).toLocaleString() : '—'}</td>
              <td><code>{r.action}</code></td>
              <td><code className="audit-payload">{summarize(r.before_redacted)}</code></td>
              <td><code className="audit-payload">{summarize(r.after_redacted)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
      {offset < total && (
        <div className="audit-load-more">
          <button type="button" onClick={() => load(false)} disabled={loading}>
            {loading ? 'Loading…' : `Load more (${rows.length}/${total})`}
          </button>
        </div>
      )}
    </div>
  );
}

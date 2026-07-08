/**
 * Paginated audit log for a connector. The backend returns
 * `{rows, total, limit, offset}` — we walk it with a simple
 * "Load more" button rather than a full page UI.
 */
import React, { useEffect, useState } from 'react';
import { AlertCircle, Loader2, ChevronDown, ClipboardList } from 'lucide-react';
import connectorService from '../../services/connectorService.js';
import Button from '../ui/Button.jsx';
import EmptyState from '../ui/EmptyState.jsx';
import { Skeleton } from '../ui/Skeleton.jsx';

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
    <div className="flex flex-col gap-3">
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3 py-2 text-sm text-[var(--danger)] flex items-start gap-2"
        >
          <AlertCircle size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
          <span className="flex-1 break-words">{error}</span>
        </div>
      )}
      {loading && !rows.length ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-9 rounded-md" />
          ))}
        </div>
      ) : !rows.length ? (
        <EmptyState
          icon={ClipboardList}
          title="No audit events yet"
          description="Changes to this connector will appear here once they're made."
        />
      ) : (
        <>
          <div className="rounded-lg border border-hairline overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-surface-2/50 sticky top-0">
                <tr className="text-left">
                  <th className="px-3 py-2 text-[10px] font-medium uppercase tracking-wider text-ink-faint">When</th>
                  <th className="px-3 py-2 text-[10px] font-medium uppercase tracking-wider text-ink-faint">Action</th>
                  <th className="px-3 py-2 text-[10px] font-medium uppercase tracking-wider text-ink-faint">Before</th>
                  <th className="px-3 py-2 text-[10px] font-medium uppercase tracking-wider text-ink-faint">After</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id || `${r.at}-${Math.random()}`} className="border-t border-hairline hover:bg-surface-2/30">
                    <td className="px-3 py-2 text-xs text-ink whitespace-nowrap align-top">
                      {r.at ? new Date(r.at).toLocaleString() : '—'}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <code className="text-[11px] bg-surface-2 px-1.5 py-0.5 rounded">
                        {r.action}
                      </code>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <code className="block max-w-[240px] max-h-[60px] overflow-auto text-[11px] bg-surface-2 px-1.5 py-1 rounded whitespace-pre-wrap break-words">
                        {summarize(r.before_redacted)}
                      </code>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <code className="block max-w-[240px] max-h-[60px] overflow-auto text-[11px] bg-surface-2 px-1.5 py-1 rounded whitespace-pre-wrap break-words">
                        {summarize(r.after_redacted)}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {offset < total && (
            <div className="flex justify-center">
              <Button onClick={() => load(false)} disabled={loading} variant="secondary" size="sm">
                {loading ? (
                  <Loader2 size={14} strokeWidth={1.75} className="animate-spin" />
                ) : (
                  <ChevronDown size={14} strokeWidth={1.75} />
                )}
                Load more ({rows.length}/{total})
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

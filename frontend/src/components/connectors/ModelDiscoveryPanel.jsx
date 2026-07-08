/**
 * Read-only list of the provider's discovered models, with a
 * "Refresh" button that re-probes the upstream. Backed by
 * `/api/connectors/{id}/models` (cached) and
 * `/api/connectors/{id}/refresh-models` (live).
 */
import React, { useEffect, useState } from 'react';
import { RefreshCw, Loader2, AlertCircle, ListChecks } from 'lucide-react';
import connectorService from '../../services/connectorService.js';
import Button from '../ui/Button.jsx';
import EmptyState from '../ui/EmptyState.jsx';
import { Skeleton } from '../ui/Skeleton.jsx';

export default function ModelDiscoveryPanel({ connector }) {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [discoveredAt, setDiscoveredAt] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await connectorService.models(connector.id);
      setModels(res?.models || []);
      setDiscoveredAt(res?.discovered_at || null);
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (connector?.id) load();
  }, [connector?.id]);

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await connectorService.refreshModels(connector.id);
      setModels(res?.models || []);
      setDiscoveredAt(res?.discovered_at || new Date().toISOString());
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-medium tracking-tight text-ink">Discovered models</h3>
        <Button onClick={refresh} disabled={refreshing} variant="secondary" size="sm">
          {refreshing ? (
            <>
              <Loader2 size={14} strokeWidth={1.75} className="animate-spin" />
              Refreshing…
            </>
          ) : (
            <>
              <RefreshCw size={14} strokeWidth={1.75} />
              Refresh
            </>
          )}
        </Button>
        {discoveredAt && (
          <span className="text-xs text-ink-faint ml-auto">
            last sync: {new Date(discoveredAt).toLocaleString()}
          </span>
        )}
      </div>
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3 py-2 text-sm text-[var(--danger)] flex items-start gap-2"
        >
          <AlertCircle size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
          <span className="flex-1 break-words">{error}</span>
        </div>
      )}
      {loading && !models.length ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-8 rounded-md" />
          ))}
        </div>
      ) : !models.length ? (
        <EmptyState
          icon={ListChecks}
          title="No models discovered yet"
          description="Click Refresh to probe the upstream provider and pull its advertised model list."
        />
      ) : (
        <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {models.map((m) => (
            <li
              key={m}
              className="rounded-md border border-hairline bg-surface-2/30 px-3 py-2 text-xs font-mono text-ink truncate"
              title={m}
            >
              {m}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

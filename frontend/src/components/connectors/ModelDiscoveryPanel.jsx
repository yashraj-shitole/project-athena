/**
 * Read-only list of the provider's discovered models, with a
 * "Refresh" button that re-probes the upstream. Backed by
 * `/api/connectors/{id}/models` (cached) and
 * `/api/connectors/{id}/refresh-models` (live).
 */
import React, { useEffect, useState } from 'react';
import connectorService from '../../services/connectorService.js';

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
    <div className="model-discovery-panel">
      <div className="model-discovery-header">
        <strong>Discovered models</strong>
        <button type="button" onClick={refresh} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
        {discoveredAt && (
          <span className="model-discovery-stamp">
            last sync: {new Date(discoveredAt).toLocaleString()}
          </span>
        )}
      </div>
      {error && <div className="test-panel-error">⚠ {error}</div>}
      {loading && !models.length ? (
        <div className="model-discovery-empty">Loading…</div>
      ) : !models.length ? (
        <div className="model-discovery-empty">
          No models discovered yet. Click "Refresh" to probe the upstream.
        </div>
      ) : (
        <ul className="model-discovery-list">
          {models.map((m) => (
            <li key={m}><code>{m}</code></li>
          ))}
        </ul>
      )}
    </div>
  );
}

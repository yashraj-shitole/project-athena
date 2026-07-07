/**
 * The "run a health check" panel. Used standalone on the
 * Connectors page header and inside the create/edit dialog.
 *
 * Accepts either a `connector` (live probe) or a `payload`
 * (test-before-save). The result envelope is the same
 * `HealthReport` shape on both paths.
 */
import React, { useState } from 'react';
import connectorService from '../../services/connectorService.js';
import HealthBadge from './HealthBadge.jsx';

export default function TestPanel({ connector, payload, onResult }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = connector
        ? // Live: the backend will resolve the row and probe.
          // We POST to `/test` with the live row's identifying fields
          // so the panel also works for unsaved edits.
          await connectorService.test({
            provider: connector.provider,
            base_url: connector.base_url,
            api_key: connector.api_key_preview || '', // preview is non-empty only for masked display
            auth_type: connector.auth_type,
            auth_header_name: connector.auth_header_name,
            organization_id: connector.organization_id,
            project_id: connector.project_id,
            api_version: connector.api_version,
            custom_headers: connector.custom_headers || {},
            default_model: connector.default_model,
          })
        : await connectorService.test(payload);
      setResult(res);
      onResult?.(res);
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="test-panel">
      <div className="test-panel-header">
        <button type="button" onClick={run} disabled={running}>
          {running ? 'Probing…' : 'Test connection'}
        </button>
        <span className="test-panel-hint">
          Sends a small probe to the upstream. The plaintext key in the form
          is only used for this request.
        </span>
      </div>
      {error && <div className="test-panel-error">⚠ {error}</div>}
      {result && (
        <div className="test-panel-result">
          <div className="test-panel-row">
            <span className="test-panel-label">Status</span>
            <HealthBadge status={result.status} latencyMs={result.latency_ms} />
          </div>
          {result.error && (
            <div className="test-panel-row">
              <span className="test-panel-label">Error</span>
              <span className="test-panel-error-text">{result.error}</span>
            </div>
          )}
          {result.category && (
            <div className="test-panel-row">
              <span className="test-panel-label">Category</span>
              <code>{result.category}</code>
            </div>
          )}
          {result.capabilities && (
            <div className="test-panel-row">
              <span className="test-panel-label">Capabilities</span>
              <code>{JSON.stringify(result.capabilities)}</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

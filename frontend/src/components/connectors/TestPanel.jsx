/**
 * The "run a health check" panel. Used standalone on the
 * Connectors page header and inside the create/edit dialog.
 *
 * Accepts either a `connector` (live probe) or a `payload`
 * (test-before-save). The result envelope is the same
 * `HealthReport` shape on both paths.
 */
import React, { useState } from 'react';
import { AlertCircle, Plug, Loader2 } from 'lucide-react';
import connectorService from '../../services/connectorService.js';
import HealthBadge from './HealthBadge.jsx';
import Button from '../ui/Button.jsx';

// `POST /api/connectors/test` is backed by the strict `TestRequest` schema,
// which inherits `extra="forbid"` and declares ONLY connection-relevant
// fields. The create/edit dialog's `payload` also carries ownership /
// visibility flags — `is_enabled`, `is_admin`, `is_favorite`, `group_name`,
// `tags` — that `TestRequest` rejects with a 422 ("Extra inputs are not
// permitted"). Pick exactly the `TestRequest` fields so the probe receives
// what it needs while the backend's forbid stays a defense-in-depth
// mass-assignment guard (a smuggled `is_admin` must never reach the route).
// Use key-MEMBERSHIP, not truthiness: `default_model` may legitimately be
// an empty string and the custom provider nests `request_template` /
// `response_paths` (possibly `null`) inside `custom_headers` — a
// truthiness filter would silently drop those and surface a confusing
// downstream error.
const TEST_FIELDS = [
  'name', 'provider', 'base_url', 'api_key', 'auth_type',
  'auth_header_name', 'organization_id', 'project_id', 'api_version',
  'custom_headers', 'default_model', 'models', 'capabilities',
  'settings', 'timeout_s',
];

function toTestPayload(payload) {
  const src = payload || {};
  const out = {};
  for (const k of TEST_FIELDS) if (k in src) out[k] = src[k];
  return out;
}

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
        : await connectorService.test(toTestPayload(payload));
      setResult(res);
      onResult?.(res);
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <Button onClick={run} disabled={running} variant="secondary" size="sm">
          {running ? (
            <>
              <Loader2 size={14} strokeWidth={1.75} className="animate-spin" />
              Probing…
            </>
          ) : (
            <>
              <Plug size={14} strokeWidth={1.75} />
              Test connection
            </>
          )}
        </Button>
        <span className="text-xs text-ink-faint">
          Sends a small probe to the upstream. The plaintext key in the
          form is only used for this request.
        </span>
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
      {result && (
        <div className="rounded-lg border border-hairline bg-surface-2/30 p-3 flex flex-col gap-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wider text-ink-faint w-24">
              Status
            </span>
            <HealthBadge status={result.status} latencyMs={result.latency_ms} />
          </div>
          {result.error && (
            <div className="flex items-start gap-2">
              <span className="text-[11px] font-medium uppercase tracking-wider text-ink-faint w-24 pt-0.5">
                Error
              </span>
              <span className="flex-1 text-[var(--danger)] break-words">{result.error}</span>
            </div>
          )}
          {result.category && (
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-medium uppercase tracking-wider text-ink-faint w-24">
                Category
              </span>
              <code className="text-xs">{result.category}</code>
            </div>
          )}
          {result.capabilities && (
            <div className="flex items-start gap-2">
              <span className="text-[11px] font-medium uppercase tracking-wider text-ink-faint w-24 pt-0.5">
                Capabilities
              </span>
              <code className="flex-1 text-xs break-words">
                {JSON.stringify(result.capabilities)}
              </code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Status pill for a connector's last health snapshot.
 *
 * Colors match the documented `last_health` values: online (green),
 * offline (gray), auth_failed (red), rate_limited (orange),
 * slow (yellow), unknown (gray).
 */
import React from 'react';
import StatusPill from '../ui/StatusPill.jsx';

const LATENCY_LABEL = {
  online: 'online',
  offline: 'offline',
  auth_failed: 'auth failed',
  rate_limited: 'rate limited',
  slow: 'slow',
  unknown: 'unknown',
};

export default function HealthBadge({ status, latencyMs }) {
  const s = (status || 'unknown').toLowerCase();
  const label = s === 'online' && latencyMs
    ? `online · ${latencyMs}ms`
    : LATENCY_LABEL[s] || s;
  return (
    <StatusPill status={s} title={latencyMs ? `Last probe: ${latencyMs}ms` : ''}>
      {label}
    </StatusPill>
  );
}

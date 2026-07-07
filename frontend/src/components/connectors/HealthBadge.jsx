/**
 * Status pill for a connector's last health snapshot.
 *
 * Colors match the documented `last_health` values: online (green),
 * offline (gray), auth_failed (red), rate_limited (orange),
 * slow (yellow), unknown (gray).
 */
import React from 'react';

const COLORS = {
  online: { bg: '#10b981', fg: '#fff' },         // emerald-500
  offline: { bg: '#6b7280', fg: '#fff' },        // gray-500
  auth_failed: { bg: '#ef4444', fg: '#fff' },    // red-500
  rate_limited: { bg: '#f97316', fg: '#fff' },   // orange-500
  slow: { bg: '#eab308', fg: '#000' },           // yellow-500
  unknown: { bg: '#9ca3af', fg: '#fff' },        // gray-400
};

export default function HealthBadge({ status, latencyMs }) {
  const s = (status || 'unknown').toLowerCase();
  const palette = COLORS[s] || COLORS.unknown;
  const label = s === 'online' && latencyMs
    ? `online · ${latencyMs}ms`
    : s;
  return (
    <span
      className="status-pill"
      style={{ background: palette.bg, color: palette.fg }}
      title={latencyMs ? `Last probe: ${latencyMs}ms` : ''}
    >
      {label}
    </span>
  );
}

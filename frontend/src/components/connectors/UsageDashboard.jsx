/**
 * Inline bar chart of daily request counts + token usage for a
 * connector over the last N days. Pure SVG, no chart library.
 * Falls back to a friendly empty-state when no data.
 */
import React, { useEffect, useState } from 'react';
import connectorService from '../../services/connectorService.js';

const WIDTH = 560;
const HEIGHT = 140;
const PADDING = { top: 12, right: 12, bottom: 24, left: 36 };

function formatNum(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export default function UsageDashboard({ connector, days = 7 }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      if (!connector?.id) return;
      setLoading(true);
      setError(null);
      try {
        const res = await connectorService.usage(connector.id, days);
        if (!cancel) setData(res);
      } catch (err) {
        if (!cancel) setError(err?.message || String(err));
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, [connector?.id, days]);

  if (loading && !data) return <div className="usage-empty">Loading usage…</div>;
  if (error) return <div className="test-panel-error">⚠ {error}</div>;
  if (!data) return null;

  const byDay = data.by_day || [];
  if (!byDay.length || !data.total_requests) {
    return (
      <div className="usage-empty">
        No usage in the last {days} days. Send a chat turn with this connector
        to see it appear here.
      </div>
    );
  }

  // Build the bar chart on the request count.
  const max = Math.max(...byDay.map((d) => d.requests || 0), 1);
  const innerW = WIDTH - PADDING.left - PADDING.right;
  const innerH = HEIGHT - PADDING.top - PADDING.bottom;
  const barW = innerW / byDay.length;

  return (
    <div className="usage-dashboard">
      <div className="usage-summary">
        <span><strong>{data.total_requests}</strong> requests</span>
        <span><strong>{formatNum(data.total_prompt_tokens)}</strong> prompt tokens</span>
        <span><strong>{formatNum(data.total_completion_tokens)}</strong> completion tokens</span>
        <span>avg latency <strong>{Math.round(data.avg_latency_ms || 0)}ms</strong></span>
        <span>success <strong>{Math.round((data.success_rate || 0) * 100)}%</strong></span>
      </div>
      <svg
        className="usage-chart"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        width="100%"
        height={HEIGHT}
        role="img"
        aria-label={`Daily request counts over the last ${days} days`}
      >
        {/* Y axis grid */}
        {[0, 0.5, 1].map((t) => {
          const y = PADDING.top + innerH * (1 - t);
          return (
            <g key={t}>
              <line
                x1={PADDING.left}
                x2={PADDING.left + innerW}
                y1={y}
                y2={y}
                stroke="#e5e7eb"
                strokeDasharray="2 4"
              />
              <text x={4} y={y + 4} fontSize="10" fill="#6b7280">
                {Math.round(max * t)}
              </text>
            </g>
          );
        })}
        {byDay.map((d, i) => {
          const h = ((d.requests || 0) / max) * innerH;
          const x = PADDING.left + i * barW + 2;
          const y = PADDING.top + innerH - h;
          const w = Math.max(2, barW - 4);
          return (
            <g key={d.day}>
              <rect x={x} y={y} width={w} height={h} rx="2" fill="#6366f1" />
              <text
                x={x + w / 2}
                y={HEIGHT - 8}
                fontSize="9"
                textAnchor="middle"
                fill="#6b7280"
              >
                {d.day?.slice(5) || ''}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

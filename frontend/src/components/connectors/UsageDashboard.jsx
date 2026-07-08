/**
 * Inline bar chart of daily request counts + token usage for a
 * connector over the last N days. Pure SVG, no chart library.
 * Falls back to a friendly empty-state when no data.
 */
import React, { useEffect, useState } from 'react';
import { AlertCircle, BarChart3, Loader2 } from 'lucide-react';
import connectorService from '../../services/connectorService.js';
import EmptyState from '../ui/EmptyState.jsx';
import { Skeleton } from '../ui/Skeleton.jsx';

const WIDTH = 560;
const HEIGHT = 160;
const PADDING = { top: 16, right: 12, bottom: 28, left: 40 };

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

  if (loading && !data) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-32 w-full rounded-md" />
      </div>
    );
  }
  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3 py-2 text-sm text-[var(--danger)] flex items-start gap-2"
      >
        <AlertCircle size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
        <span className="flex-1 break-words">{error}</span>
      </div>
    );
  }
  if (!data) return null;

  const byDay = data.by_day || [];
  if (!byDay.length || !data.total_requests) {
    return (
      <EmptyState
        icon={BarChart3}
        title="No usage yet"
        description={`No usage in the last ${days} days. Send a chat turn with this connector to see it appear here.`}
      />
    );
  }

  // Build the bar chart on the request count.
  const max = Math.max(...byDay.map((d) => d.requests || 0), 1);
  const innerW = WIDTH - PADDING.left - PADDING.right;
  const innerH = HEIGHT - PADDING.top - PADDING.bottom;
  const barW = innerW / byDay.length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
        <Stat label="Requests" value={formatNum(data.total_requests)} />
        <Stat label="Prompt tokens" value={formatNum(data.total_prompt_tokens)} />
        <Stat label="Completion tokens" value={formatNum(data.total_completion_tokens)} />
        <Stat label="Avg latency" value={`${Math.round(data.avg_latency_ms || 0)}ms`} />
        <Stat label="Success" value={`${Math.round((data.success_rate || 0) * 100)}%`} />
      </div>
      <div className="rounded-lg border border-hairline bg-surface-2/30 p-3">
        <svg
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
                  stroke="rgba(28, 28, 28, 0.08)"
                  strokeDasharray="2 4"
                />
                <text x={4} y={y + 4} fontSize="10" fill="rgba(28, 28, 28, 0.4)">
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
                <rect x={x} y={y} width={w} height={h} rx="2" fill="var(--accent)" opacity="0.85" />
                <text
                  x={x + w / 2}
                  y={HEIGHT - 10}
                  fontSize="9"
                  textAnchor="middle"
                  fill="rgba(28, 28, 28, 0.4)"
                >
                  {d.day?.slice(5) || ''}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] font-medium uppercase tracking-wider text-ink-faint">
        {label}
      </span>
      <span className="text-base font-medium text-ink tabular-nums">{value}</span>
    </div>
  );
}

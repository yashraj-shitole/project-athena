import React from 'react';
import { cn } from '../../lib/cn.js';

/**
 * StatusPill — a small uppercase label that shows the state of
 * something (document status, connector health). Sized tightly and
 * with a soft tinted background so it never screams.
 */
const STATUS_TONE = {
  // Document lifecycle.
  uploaded:   'bg-[var(--info-bg)] text-[var(--info)]',
  processing: 'bg-[var(--warn-bg)] text-[var(--warn)]',
  indexed:    'bg-[var(--ok-bg)] text-[var(--ok)]',
  failed:     'bg-[var(--danger-bg)] text-[var(--danger)]',
  // Connector health.
  online:        'bg-[var(--ok-bg)] text-[var(--ok)]',
  offline:       'bg-surface-2 text-ink-dim',
  auth_failed:   'bg-[var(--danger-bg)] text-[var(--danger)]',
  rate_limited:  'bg-[var(--warn-bg)] text-[var(--warn)]',
  slow:          'bg-[var(--warn-bg)] text-[var(--warn)]',
  unknown:       'bg-surface-2 text-ink-dim',
};

export function StatusPill({ status, children, className, ...props }) {
  const tone = STATUS_TONE[status] || STATUS_TONE.unknown;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5',
        'text-[10px] font-medium uppercase tracking-wider',
        tone,
        className,
      )}
      {...props}
    >
      {children ?? status}
    </span>
  );
}

export default StatusPill;

import React from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '../../lib/cn.js';

/**
 * Badge — a small, soft-tinted pill. Used for capabilities, tags, and
 * counts. Variants tone the badge; size controls the metric.
 */
const badgeStyles = cva(
  'inline-flex items-center gap-1.5 rounded-full font-medium tracking-tight whitespace-nowrap',
  {
    variants: {
      tone: {
        neutral: 'bg-surface-2 text-ink-dim border border-hairline',
        ok:      'bg-[var(--ok-bg)] text-[var(--ok)]',
        warn:    'bg-[var(--warn-bg)] text-[var(--warn)]',
        danger:  'bg-[var(--danger-bg)] text-[var(--danger)]',
        info:    'bg-[var(--info-bg)] text-[var(--info)]',
        solid:   'bg-[var(--accent)] text-[var(--accent-fg)]',
      },
      size: {
        sm: 'h-5 px-2 text-[10px] uppercase tracking-wider',
        md: 'h-6 px-2.5 text-xs',
        lg: 'h-7 px-3 text-sm',
      },
    },
    defaultVariants: { tone: 'neutral', size: 'md' },
  },
);

export function Badge({ className, tone, size, children, ...props }) {
  return (
    <span className={cn(badgeStyles({ tone, size }), className)} {...props}>
      {children}
    </span>
  );
}

export default Badge;

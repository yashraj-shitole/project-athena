import React from 'react';
import { cn } from '../../lib/cn.js';

/**
 * EmptyState — the brief calls for an illustration, helpful
 * explanation, primary action, and optional secondary action on every
 * empty state. We accept a Lucide icon for the illustration; the
 * rest is plain text and consumers' buttons.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  primaryAction,
  secondaryAction,
  className,
  ...props
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        'rounded-xl border border-dashed border-hairline bg-surface/50',
        'px-6 py-12',
        className,
      )}
      {...props}
    >
      {Icon && (
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface-2 text-ink-dim">
          <Icon size={22} strokeWidth={1.5} />
        </div>
      )}
      {title && (
        <h3 className="text-base font-medium tracking-tight text-ink">{title}</h3>
      )}
      {description && (
        <p className="mt-1.5 max-w-md text-sm text-ink-dim leading-relaxed">{description}</p>
      )}
      {(primaryAction || secondaryAction) && (
        <div className="mt-5 flex items-center gap-2">
          {primaryAction}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}

export default EmptyState;

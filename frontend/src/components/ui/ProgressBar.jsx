import React from 'react';
import { cn } from '../../lib/cn.js';

/**
 * ProgressBar — a slim, warm progress bar. `percent` is 0..100
 * (clamped). Indeterminate mode shows a slow shimmer — used when
 * a stage has no number yet.
 */
export function ProgressBar({
  percent,
  label,
  indeterminate = false,
  tone = 'accent',
  className,
  ...props
}) {
  const safe = typeof percent === 'number' ? Math.max(0, Math.min(100, percent)) : 0;
  const display = typeof percent === 'number' ? `${Math.round(safe)}%` : '';
  const fillBg =
    tone === 'danger' ? 'var(--danger)' :
    tone === 'success' ? 'var(--ok)' :
    'var(--accent)';

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(safe)}
      aria-label={label || 'Progress'}
      className={cn(
        'relative h-1.5 w-full overflow-hidden rounded-full bg-surface-2',
        className,
      )}
      {...props}
    >
      {indeterminate ? (
        <div
          className="absolute inset-y-0 w-1/3 rounded-full"
          style={{
            background: fillBg,
            animation: 'shimmer 1.4s ease-in-out infinite',
            background:
              'linear-gradient(90deg, transparent, var(--accent) 50%, transparent)',
            backgroundSize: '200% 100%',
          }}
        />
      ) : (
        <div
          className="h-full rounded-full transition-[width] duration-300 ease-out"
          style={{ width: `${safe}%`, background: fillBg }}
        />
      )}
      {(label || display) && (
        <div className="mt-1.5 flex items-center justify-between text-[11px] text-ink-dim">
          {label && <span>{label}</span>}
          {display && <span className="font-medium text-ink tabular-nums">{display}</span>}
        </div>
      )}
    </div>
  );
}

export default ProgressBar;

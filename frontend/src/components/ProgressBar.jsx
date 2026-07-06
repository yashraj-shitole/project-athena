import React from 'react';

/**
 * Slim progress bar with a centered % label.
 *
 * `percent` is 0..100 (clamped). A "indeterminate" state (no number)
 * shows an animated stripe — useful for stages where the backend
 * doesn't emit a percentage (e.g. while waiting for the FIRST
 * embedding batch on a tiny doc).
 */
export default function ProgressBar({ percent, label, indeterminate = false, tone = 'accent' }) {
  const safe = typeof percent === 'number' ? Math.max(0, Math.min(100, percent)) : 0;
  const display = typeof percent === 'number' ? `${Math.round(safe)}%` : '';
  return (
    <div
      className={`progress-bar progress-bar-${tone}${indeterminate ? ' is-indeterminate' : ''}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(safe)}
      aria-label={label || 'Progress'}
    >
      <div
        className="progress-bar-fill"
        style={{ width: indeterminate ? '40%' : `${safe}%` }}
      />
      {display && <span className="progress-bar-label">{display}</span>}
      {label && !display && <span className="progress-bar-label">{label}</span>}
    </div>
  );
}

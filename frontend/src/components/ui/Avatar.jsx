import React from 'react';
import { cn } from '../../lib/cn.js';

/**
 * Avatar — a small circular monogram. Used in the sidebar header for
 * the logged-in user.
 */
export function Avatar({ name, size = 32, className, ...props }) {
  const initials = (name || '?')
    .split(/\s+/)
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
  return (
    <div
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full',
        'bg-surface-2 text-ink-dim font-medium tracking-tight',
        className,
      )}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.4) }}
      aria-label={name}
      {...props}
    >
      {initials}
    </div>
  );
}

export default Avatar;

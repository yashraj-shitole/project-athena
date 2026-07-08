import React, { forwardRef } from 'react';
import { cn } from '../../lib/cn.js';

/**
 * Select — a styled native <select>. We keep native so accessibility
 * and mobile pickers come for free; we only restyle the chrome.
 */
export const Select = forwardRef(function Select({ className, children, ...props }, ref) {
  return (
    <div className="relative">
      <select
        ref={ref}
        className={cn(
          'flex h-9 w-full appearance-none rounded-lg border border-hairline bg-surface pl-3 pr-9 text-sm',
          'text-ink',
          'transition-colors duration-[var(--motion-fast)] ease-out',
          'hover:border-hairline-strong/60',
          'focus:outline-none focus:border-hairline-strong',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <svg
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-dim"
        width="12" height="12" viewBox="0 0 12 12" fill="none"
      >
        <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
});

export default Select;

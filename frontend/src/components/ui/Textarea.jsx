import React, { forwardRef } from 'react';
import { cn } from '../../lib/cn.js';

export const Textarea = forwardRef(function Textarea({ className, rows = 3, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(
        'flex w-full rounded-lg border border-hairline bg-surface px-3 py-2.5 text-sm',
        'text-ink placeholder:text-ink-faint',
        'transition-colors duration-[var(--motion-fast)] ease-out',
        'hover:border-hairline-strong/60',
        'focus:outline-none focus:border-hairline-strong',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        'resize-y',
        className,
      )}
      {...props}
    />
  );
});

export default Textarea;

import React from 'react';
import { cn } from '../../lib/cn.js';

/**
 * Topbar — the slim header bar above a page's main content. Holds a
 * title or breadcrumbs on the left and arbitrary actions on the
 * right. Stays under a single hairline — no chrome.
 */
export function Topbar({ children, className }) {
  return (
    <header
      className={cn(
        'flex items-center justify-between gap-3',
        'h-14 px-6 border-b border-hairline bg-surface/40 backdrop-blur-sm',
        'shrink-0',
        className,
      )}
    >
      {children}
    </header>
  );
}

export default Topbar;

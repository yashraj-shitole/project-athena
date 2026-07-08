import React from 'react';
import * as RTabs from '@radix-ui/react-tabs';
import { cn } from '../../lib/cn.js';

/**
 * Tabs — Radix Tabs styled with the warm-paper language. The tab
 * strip is a single hairline with an underline indicator (no chrome).
 */
export function Tabs({ defaultValue, value, onValueChange, className, children }) {
  return (
    <RTabs.Root
      defaultValue={defaultValue}
      value={value}
      onValueChange={onValueChange}
      className={cn('flex flex-col gap-4', className)}
    >
      {children}
    </RTabs.Root>
  );
}

export function TabsList({ className, children, ...props }) {
  return (
    <RTabs.List
      className={cn(
        'flex items-center gap-1 border-b border-hairline',
        className,
      )}
      {...props}
    >
      {children}
    </RTabs.List>
  );
}

export function TabsTrigger({ value, className, children, ...props }) {
  return (
    <RTabs.Trigger
      value={value}
      className={cn(
        'relative inline-flex items-center gap-2 px-3 py-2 text-sm',
        'text-ink-dim',
        'transition-colors duration-[var(--motion-fast)]',
        'hover:text-ink',
        'data-[state=active]:text-ink',
        'data-[state=active]:after:absolute data-[state=active]:after:inset-x-0 data-[state=active]:after:-bottom-px data-[state=active]:after:h-[2px] data-[state=active]:after:bg-[var(--accent)] data-[state=active]:after:rounded-full',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hairline-strong focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        className,
      )}
      {...props}
    >
      {children}
    </RTabs.Trigger>
  );
}

export function TabsContent({ value, className, children, ...props }) {
  return (
    <RTabs.Content
      value={value}
      className={cn('focus-visible:outline-none', className)}
      {...props}
    >
      {children}
    </RTabs.Content>
  );
}

export default Tabs;

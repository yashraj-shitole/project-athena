import React from 'react';
import * as RTooltip from '@radix-ui/react-tooltip';
import { cn } from '../../lib/cn.js';

/**
 * TooltipProvider — wrap your tree once so tooltips work everywhere.
 * We delay 200ms by default; long enough to not flash on hover, short
 * enough to feel responsive.
 */
export function TooltipProvider({ children, delayDuration = 250 }) {
  return (
    <RTooltip.Provider delayDuration={delayDuration} skipDelayDuration={300}>
      {children}
    </RTooltip.Provider>
  );
}

export function Tooltip({ children, content, side = 'top', align = 'center', className }) {
  return (
    <RTooltip.Root>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content
          side={side}
          align={align}
          sideOffset={6}
          className={cn(
            'z-50 rounded-md bg-[var(--accent)] text-[var(--accent-fg)] px-2 py-1',
            'text-xs font-medium tracking-tight',
            'shadow-soft',
            'data-[state=delayed-open]:animate-fade-in',
            className,
          )}
        >
          {content}
          <RTooltip.Arrow className="fill-[var(--accent)]" />
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

export default Tooltip;

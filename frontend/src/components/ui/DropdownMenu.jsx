import React from 'react';
import * as RDropdown from '@radix-ui/react-dropdown-menu';
import { Check } from 'lucide-react';
import { cn } from '../../lib/cn.js';

/**
 * DropdownMenu — a single-select or action menu. Use this for any
 * "..." overflow that doesn't need a separate control.
 */
export function DropdownMenu({ children, trigger, align = 'end', side = 'bottom' }) {
  return (
    <RDropdown.Root>
      <RDropdown.Trigger asChild>{trigger}</RDropdown.Trigger>
      <RDropdown.Portal>
        <RDropdown.Content
          align={align}
          side={side}
          sideOffset={6}
          className={cn(
            'z-50 min-w-[180px] overflow-hidden rounded-lg border border-hairline bg-surface',
            'shadow-floating p-1',
            'data-[state=open]:animate-fade-in',
          )}
        >
          {children}
        </RDropdown.Content>
      </RDropdown.Portal>
    </RDropdown.Root>
  );
}

export function DropdownItem({ children, onSelect, disabled, danger, asChild, className, ...props }) {
  return (
    <RDropdown.Item
      onSelect={onSelect}
      disabled={disabled}
      asChild={asChild}
      className={cn(
        'flex w-full cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm',
        'text-ink outline-none',
        'data-[highlighted]:bg-surface-2',
        danger && 'text-[var(--danger)] data-[highlighted]:bg-[var(--danger-bg)]',
        'data-[disabled]:opacity-50 data-[disabled]:cursor-not-allowed',
        className,
      )}
      {...props}
    >
      {children}
    </RDropdown.Item>
  );
}

export function DropdownSeparator() {
  return <RDropdown.Separator className="my-1 h-px bg-hairline" />;
}

export function DropdownLabel({ children }) {
  return (
    <RDropdown.Label className="px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-ink-faint">
      {children}
    </RDropdown.Label>
  );
}

export function DropdownCheckboxItem({ children, checked, onCheckedChange, onSelect, disabled }) {
  return (
    <RDropdown.CheckboxItem
      checked={checked}
      onCheckedChange={onCheckedChange}
      onSelect={(e) => { e.preventDefault(); onSelect?.(checked); }}
      disabled={disabled}
      className={cn(
        'flex w-full cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm',
        'text-ink outline-none',
        'data-[highlighted]:bg-surface-2',
        'data-[disabled]:opacity-50 data-[disabled]:cursor-not-allowed',
      )}
    >
      <span className="flex h-3.5 w-3.5 items-center justify-center text-[var(--accent)]">
        <RDropdown.ItemIndicator>
          <Check size={14} strokeWidth={2} />
        </RDropdown.ItemIndicator>
      </span>
      {children}
    </RDropdown.CheckboxItem>
  );
}

export default DropdownMenu;

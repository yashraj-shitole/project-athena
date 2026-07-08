import React, { forwardRef } from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '../../lib/cn.js';

/**
 * Button — the only button in the design system. Variants: primary,
 * secondary, ghost, outline, danger, link. Sizes: sm, md, lg, icon.
 *
 * The "signature inset" from the brief is rendered as a soft inner
 * shadow on primary buttons, giving them a pressed-paper feel.
 */
const buttonStyles = cva(
  [
    'inline-flex items-center justify-center gap-2 whitespace-nowrap',
    'font-medium tracking-tight rounded-lg',
    'transition-colors duration-[var(--motion-fast)] ease-out',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hairline-strong focus-visible:ring-offset-2 focus-visible:ring-offset-background',
    'disabled:pointer-events-none disabled:opacity-50',
    'select-none',
  ],
  {
    variants: {
      variant: {
        primary: [
          'bg-[var(--accent)] text-[var(--accent-fg)]',
          'hover:bg-[#2a2a2a]',
          'active:bg-[#0e0e0e]',
          'shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_1px_2px_rgba(28,28,28,0.12)]',
        ],
        secondary: [
          'bg-surface text-ink',
          'border border-hairline',
          'hover:border-hairline-strong hover:bg-surface-2',
        ],
        ghost: [
          'bg-transparent text-ink',
          'hover:bg-surface-2',
        ],
        outline: [
          'bg-transparent text-ink',
          'border border-hairline',
          'hover:border-hairline-strong hover:bg-surface',
        ],
        danger: [
          'bg-[var(--danger-bg)] text-[var(--danger)]',
          'border border-[var(--danger)]/40',
          'hover:bg-[var(--danger)] hover:text-white',
        ],
        link: [
          'bg-transparent text-ink underline-offset-4',
          'hover:underline',
          'rounded-none px-0 h-auto',
        ],
      },
      size: {
        sm:  'h-8 px-3 text-sm',
        md:  'h-9 px-4 text-sm',
        lg:  'h-11 px-6 text-base',
        icon: 'h-9 w-9 p-0',
        'icon-sm': 'h-8 w-8 p-0',
        'icon-lg': 'h-11 w-11 p-0',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

export const Button = forwardRef(function Button(
  { className, variant, size, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(buttonStyles({ variant, size }), className)}
      {...props}
    />
  );
});

export default Button;

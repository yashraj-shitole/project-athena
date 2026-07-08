import React, { forwardRef } from 'react';
import { cn } from '../../lib/cn.js';

/**
 * Input — a single-line text input. Pair with `Label` and helper text
 * for accessible forms. Focus state is a hairline-strong border, no
 * halo, in keeping with the editorial language.
 */
export const Input = forwardRef(function Input({ className, type = 'text', ...props }, ref) {
  return (
    <input
      ref={ref}
      type={type}
      className={cn(
        'flex h-9 w-full rounded-lg border border-hairline bg-surface px-3 text-sm',
        'text-ink placeholder:text-ink-faint',
        'transition-colors duration-[var(--motion-fast)] ease-out',
        'hover:border-hairline-strong/60',
        'focus:outline-none focus:border-hairline-strong',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        className,
      )}
      {...props}
    />
  );
});

/**
 * Label — for forms. Pairs with a control by `htmlFor`.
 */
export function Label({ className, children, hint, error, ...props }) {
  return (
    <label className="flex flex-col gap-1.5" {...props}>
      <span className="text-xs font-medium uppercase tracking-wider text-ink-dim">
        {children}
        {hint && <span className="ml-1 text-ink-faint normal-case tracking-normal">· {hint}</span>}
      </span>
      <slot />
    </label>
  );
}

/**
 * FormField — composes Label + Input + helper text. Use this for
 * most text fields; reach for Input directly only when layout is custom.
 */
export const FormField = forwardRef(function FormField(
  { label, hint, error, children, className, id, ...props },
  ref,
) {
  const inputId = id || `field-${React.useId()}`;
  return (
    <div className={cn('flex flex-col gap-1.5', className)} {...props}>
      {label && (
        <label
          htmlFor={inputId}
          className="text-xs font-medium uppercase tracking-wider text-ink-dim"
        >
          {label}
          {hint && <span className="ml-1 text-ink-faint normal-case tracking-normal">· {hint}</span>}
        </label>
      )}
      {React.isValidElement(children)
        ? React.cloneElement(children, { id: inputId, ref })
        : children}
      {error && (
        <p role="alert" className="text-xs text-[var(--danger)] mt-0.5">
          {error}
        </p>
      )}
    </div>
  );
});

export default Input;

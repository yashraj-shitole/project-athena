import React, { forwardRef } from 'react';
import { cn } from '../../lib/cn.js';

/**
 * Card — a warm-paper surface with a hairline border and no shadow by
 * default. Composed via sub-components (`Card.Header`, `Card.Body`,
 * `Card.Footer`, `Card.Title`, `Card.Description`) so the consumer
 * doesn't fight nested divs.
 */
export const Card = forwardRef(function Card({ className, ...props }, ref) {
  return (
    <div
      ref={ref}
      className={cn(
        'rounded-xl border border-hairline bg-surface text-ink',
        'transition-colors duration-[var(--motion-fast)] ease-out',
        className,
      )}
      {...props}
    />
  );
});

export const CardHeader = forwardRef(function CardHeader({ className, ...props }, ref) {
  return (
    <div
      ref={ref}
      className={cn('flex items-start justify-between gap-3 px-5 pt-5', className)}
      {...props}
    />
  );
});

export const CardTitle = forwardRef(function CardTitle({ className, ...props }, ref) {
  return (
    <h3
      ref={ref}
      className={cn('text-base font-medium tracking-tight text-ink', className)}
      {...props}
    />
  );
});

export const CardDescription = forwardRef(function CardDescription({ className, ...props }, ref) {
  return (
    <p
      ref={ref}
      className={cn('text-sm text-ink-dim mt-1 leading-relaxed', className)}
      {...props}
    />
  );
});

export const CardBody = forwardRef(function CardBody({ className, ...props }, ref) {
  return <div ref={ref} className={cn('px-5 py-4', className)} {...props} />;
});

export const CardFooter = forwardRef(function CardFooter({ className, ...props }, ref) {
  return (
    <div
      ref={ref}
      className={cn(
        'flex items-center justify-between gap-2 px-5 py-4 border-t border-hairline',
        className,
      )}
      {...props}
    />
  );
});

Card.Header = CardHeader;
Card.Title = CardTitle;
Card.Description = CardDescription;
Card.Body = CardBody;
Card.Footer = CardFooter;

export default Card;

import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge Tailwind class names. We use `clsx` for conditional logic and
 * `tailwind-merge` to resolve conflicts (e.g. `p-2 p-4` → `p-4`).
 *
 * Components throughout the app pass their own classes through `cn`
 * so consumers can override variants without specificity wars.
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export default cn;

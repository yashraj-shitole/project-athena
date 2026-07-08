import React from 'react';
import * as RDialog from '@radix-ui/react-dialog';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '../../lib/cn.js';
import { scaleIn, fadeIn } from './Motion.jsx';

/**
 * Dialog — a centered modal for confirmations and short forms. For
 * full-page flows (e.g. the create-connector form) prefer Sheet.
 *
 * Built on Radix Dialog for focus management, escape-to-close, and
 * body scroll lock. Visual layer uses Framer Motion for the entrance.
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = 'md',
  className,
}) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <RDialog.Portal forceMount>
            <RDialog.Overlay asChild>
              <motion.div
                className="fixed inset-0 z-50 bg-black/30 backdrop-blur-[2px]"
                variants={fadeIn}
                initial="hidden"
                animate="show"
                exit="exit"
              />
            </RDialog.Overlay>
            <RDialog.Content asChild>
              <motion.div
                className={cn(
                  'fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)]',
                  // Cap height to the viewport so a tall dialog (e.g. the
                  // xl connector form) never overflows off-screen. The box
                  // is a flex column so the header (title/description)
                  // and footer stay pinned and the middle children region
                  // scrolls instead of being clipped — Radix's body scroll
                  // lock would otherwise make the clipped content
                  // unreachable.
                  'max-h-[calc(100vh-2rem)] flex flex-col overflow-hidden',
                  'rounded-2xl border border-hairline bg-surface',
                  'shadow-floating',
                  'p-6',
                  size === 'sm' && 'max-w-sm',
                  size === 'md' && 'max-w-md',
                  size === 'lg' && 'max-w-xl',
                  size === 'xl' && 'max-w-3xl',
                  className,
                )}
                // Center via Framer Motion's own transform, NOT Tailwind's
                // -translate-x-1/2 -translate-y-1/2. Those utilities compose
                // into the class-level `transform` property (Tailwind v3 uses
                // --tw-translate-x/y vars), but `variants={scaleIn}` makes
                // Framer write the animated `scale` as an INLINE style.transform
                // — inline wins, the -50%/-50% translate is dropped, and the
                // dialog's top-left corner lands at viewport center (down-right
                // of true center). Moving the translate into Framer's x/y lets
                // it compose `translateX(-50%) translateY(-50%) scale(...)` into
                // the one inline transform it controls, so centering survives
                // the hidden/show/exit animation and at rest. scaleIn (Motion.jsx)
                // never sets x/y, so the -50%/-50% persists across all variants.
                style={{ x: '-50%', y: '-50%' }}
                variants={scaleIn}
                initial="hidden"
                animate="show"
                exit="exit"
              >
                {title && (
                  <RDialog.Title className="shrink-0 text-lg font-medium tracking-tight text-ink">
                    {title}
                  </RDialog.Title>
                )}
                {description && (
                  <RDialog.Description className="shrink-0 mt-1.5 text-sm text-ink-dim leading-relaxed">
                    {description}
                  </RDialog.Description>
                )}
                <RDialog.Close
                  aria-label="Close"
                  className="absolute right-3 top-3 inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-dim hover:bg-surface-2 hover:text-ink transition-colors"
                >
                  <X size={16} strokeWidth={1.75} />
                </RDialog.Close>
                <div
                  className={cn(
                    // The scroll region: grows to fill the column, but
                    // can shrink below its content (min-h-0) and scroll
                    // so a tall body never pushes the footer off-screen
                    // or gets clipped by the box's max-h.
                    'flex-1 min-h-0 overflow-y-auto',
                    title || description ? 'mt-4' : '',
                  )}
                >
                  {children}
                </div>
                {footer && (
                  <div className="mt-6 shrink-0 flex items-center justify-end gap-2">
                    {footer}
                  </div>
                )}
              </motion.div>
            </RDialog.Content>
          </RDialog.Portal>
        )}
      </AnimatePresence>
    </RDialog.Root>
  );
}

/**
 * Headless trigger — exposes Radix's Trigger as a re-export so
 * consumers don't need to import from the radix package directly.
 */
export const DialogTrigger = RDialog.Trigger;
export const DialogClose = RDialog.Close;

export default Dialog;

import React from 'react';
import * as RDialog from '@radix-ui/react-dialog';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '../../lib/cn.js';
import { slideInRight, fadeIn } from './Motion.jsx';

/**
 * Sheet — a right-side drawer for secondary detail views (e.g. the
 * per-connector tabs). Same Radix backbone as Dialog, but slides in
 * from the edge.
 */
export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  width = 'md',
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
                  'fixed right-0 top-0 z-50 h-full bg-surface border-l border-hairline',
                  'shadow-floating',
                  'flex flex-col',
                  width === 'sm' && 'w-[360px] max-w-[90vw]',
                  width === 'md' && 'w-[480px] max-w-[90vw]',
                  width === 'lg' && 'w-[640px] max-w-[90vw]',
                  width === 'xl' && 'w-[820px] max-w-[90vw]',
                  className,
                )}
                variants={slideInRight}
                initial="hidden"
                animate="show"
                exit="exit"
              >
                <div className="flex items-start justify-between gap-3 px-6 py-5 border-b border-hairline">
                  <div className="min-w-0">
                    {title && (
                      <RDialog.Title className="text-lg font-medium tracking-tight text-ink">
                        {title}
                      </RDialog.Title>
                    )}
                    {description && (
                      <RDialog.Description className="mt-1 text-sm text-ink-dim leading-relaxed">
                        {description}
                      </RDialog.Description>
                    )}
                  </div>
                  <RDialog.Close
                    aria-label="Close"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-dim hover:bg-surface-2 hover:text-ink transition-colors"
                  >
                    <X size={16} strokeWidth={1.75} />
                  </RDialog.Close>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-5">
                  {children}
                </div>
                {footer && (
                  <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-hairline">
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

export const SheetTrigger = RDialog.Trigger;
export const SheetClose = RDialog.Close;

export default Sheet;

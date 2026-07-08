import React from 'react';
import { motion } from 'framer-motion';
import { cn } from '../../lib/cn.js';
import { pageEnter } from './Motion.jsx';

/**
 * PageHeader — the editorial title + optional blurb + actions block
 * at the top of a page. Uses a slightly larger tracking-tight heading
 * for the "calm" feel the brief asks for.
 */
export function PageHeader({ title, blurb, eyebrow, actions, children, className }) {
  return (
    <motion.div
      variants={pageEnter}
      initial="hidden"
      animate="show"
      className={cn('flex items-start justify-between gap-6', className)}
    >
      <div className="min-w-0 flex-1">
        {eyebrow && (
          <p className="text-xs font-medium uppercase tracking-wider text-ink-faint mb-2">
            {eyebrow}
          </p>
        )}
        {title && (
          <h1 className="text-h1 font-medium tracking-tight text-ink text-balance">
            {title}
          </h1>
        )}
        {blurb && (
          <p className="mt-2 max-w-2xl text-sm text-ink-dim leading-relaxed text-pretty">
            {blurb}
          </p>
        )}
      </div>
      {(actions || children) && (
        <div className="flex items-center gap-2 shrink-0">
          {actions}
          {children}
        </div>
      )}
    </motion.div>
  );
}

export default PageHeader;

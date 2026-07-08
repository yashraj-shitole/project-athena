import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Check, Loader2, X, Circle } from 'lucide-react';
import { cn } from '../lib/cn.js';

/**
 * Ordered list of pipeline stages, each with an icon, label, and
 * per-stage % badge. Used inside the document card while the doc is
 * being processed.
 *
 * Props:
 *   stages     [{ key, label }]   ordered list of all stages
 *   currentStage    string|null  which stage is active right now
 *   stageProgress   {[k]: pct}   per-stage percentage map
 *   status          string       'uploaded' | 'processing' | 'indexed' | 'failed'
 */
export default function StageChecklist({ stages, currentStage, stageProgress, status }) {
  const isDone = status === 'indexed';
  const isFailed = status === 'failed';
  const currentIdx = currentStage
    ? stages.findIndex((s) => s.key === currentStage)
    : -1;
  return (
    <ul className="flex flex-col gap-1.5" aria-label="Processing stages">
      <AnimatePresence initial={false}>
        {stages.map((s, i) => {
          const isCurrent = i === currentIdx;
          const isPast = isDone || i < currentIdx;
          const isFailedStage = isFailed && i === currentIdx;
          const pct = stageProgress?.[s.key];
          return (
            <motion.li
              key={s.key}
              layout
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              className={cn(
                'flex items-center gap-3 px-3 py-1.5 rounded-md text-sm',
                isCurrent && !isFailedStage && 'bg-surface-2',
                isFailedStage && 'bg-[var(--danger-bg)]',
              )}
            >
              <StageIcon
                isPast={isPast}
                isCurrent={isCurrent && !isFailedStage}
                isFailed={isFailedStage}
              />
              <span
                className={cn(
                  'flex-1 transition-colors',
                  isPast || isCurrent ? 'text-ink' : 'text-ink-faint',
                  isFailedStage && 'text-[var(--danger)]',
                )}
              >
                {s.label}
              </span>
              {typeof pct === 'number' && (
                <span
                  className={cn(
                    'text-xs tabular-nums',
                    isCurrent ? 'text-ink' : 'text-ink-dim',
                  )}
                >
                  {Math.round(pct)}%
                </span>
              )}
              {isCurrent && typeof pct !== 'number' && (
                <span className="text-xs italic text-ink-dim">running…</span>
              )}
            </motion.li>
          );
        })}
      </AnimatePresence>
    </ul>
  );
}

function StageIcon({ isPast, isCurrent, isFailed }) {
  if (isFailed) {
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--danger)] text-white">
        <X size={12} strokeWidth={2.25} />
      </span>
    );
  }
  if (isPast) {
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--ok)] text-white">
        <Check size={12} strokeWidth={2.25} />
      </span>
    );
  }
  if (isCurrent) {
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--accent)] text-[var(--accent-fg)]">
        <Loader2 size={12} strokeWidth={2.25} className="animate-spin" />
      </span>
    );
  }
  return (
    <span className="flex h-5 w-5 items-center justify-center text-ink-faint">
      <Circle size={10} strokeWidth={2} />
    </span>
  );
}

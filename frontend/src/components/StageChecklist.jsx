import React from 'react';

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
    <ul className="stage-checklist" aria-label="Processing stages">
      {stages.map((s, i) => {
        const isCurrent = i === currentIdx;
        const isPast = isDone || i < currentIdx;
        const isFailedStage = isFailed && i === currentIdx;
        const pct = stageProgress?.[s.key];
        const cls = [
          'stage-item',
          isPast && 'stage-done',
          isCurrent && !isFailedStage && 'stage-active',
          isFailedStage && 'stage-failed',
          !isPast && !isCurrent && 'stage-pending',
        ]
          .filter(Boolean)
          .join(' ');
        const icon = isFailedStage ? '✗' : isPast ? '✓' : isCurrent ? '◐' : '○';
        return (
          <li key={s.key} className={cls}>
            <span className="stage-icon" aria-hidden="true">{icon}</span>
            <span className="stage-label">{s.label}</span>
            {typeof pct === 'number' && (
              <span className="stage-pct">{Math.round(pct)}%</span>
            )}
            {isCurrent && typeof pct !== 'number' && (
              <span className="stage-pct stage-pct-running">running…</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

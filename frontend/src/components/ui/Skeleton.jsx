import React from 'react';
import { cn } from '../../lib/cn.js';

/**
 * Skeleton — a shimmer placeholder. Use for loading states instead
 * of spinners wherever possible (per the brief).
 */
export function Skeleton({ className, ...props }) {
  return <div className={cn('skeleton', className)} {...props} />;
}

/**
 * SkeletonText — a row of skeletons that mimic a body of text.
 */
export function SkeletonText({ lines = 3, className }) {
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className="h-3"
          style={{ width: `${100 - i * 12}%` }}
        />
      ))}
    </div>
  );
}

export default Skeleton;

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '../../lib/cn.js';

/**
 * Markdown — wraps react-markdown with the warm-paper prose styles.
 * Code blocks, blockquotes, lists, tables all get editorial spacing.
 */
export function Markdown({ children, className }) {
  return (
    <div
      className={cn(
        'prose prose-sm max-w-none',
        'prose-headings:font-medium prose-headings:tracking-tight prose-headings:text-ink',
        'prose-p:text-ink prose-p:leading-relaxed',
        'prose-strong:text-ink prose-strong:font-medium',
        'prose-a:text-ink prose-a:underline prose-a:underline-offset-4',
        'prose-code:text-ink prose-code:bg-surface-2 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:font-mono prose-code:text-[0.85em] prose-code:before:content-none prose-code:after:content-none',
        'prose-pre:bg-surface-2 prose-pre:border prose-pre:border-hairline prose-pre:rounded-lg',
        'prose-blockquote:border-l-hairline prose-blockquote:text-ink-dim',
        'prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5',
        'prose-table:border-collapse prose-th:border-b prose-th:border-hairline prose-th:text-left prose-th:font-medium prose-td:border-b prose-td:border-hairline',
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {children || ''}
      </ReactMarkdown>
    </div>
  );
}

export default Markdown;

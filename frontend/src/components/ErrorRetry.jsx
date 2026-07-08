import React from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';
import Button from './ui/Button.jsx';

/**
 * Inline error card with a Retry Processing button. Used inside the
 * document card when a doc's status is `failed`. The Retry button
 * fires `onRetry`; the parent owns the request.
 */
export default function ErrorRetry({ error, onRetry, retrying = false }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-4 py-3 flex flex-col gap-2"
    >
      <div className="flex items-start gap-2">
        <AlertCircle size={16} strokeWidth={1.75} className="mt-0.5 text-[var(--danger)] shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-[var(--danger)]">Processing failed</p>
          <p className="text-sm text-ink leading-relaxed break-words">
            {error || 'An unknown error occurred during ingestion.'}
          </p>
        </div>
      </div>
      {onRetry && (
        <Button
          variant="danger"
          size="sm"
          onClick={onRetry}
          disabled={retrying}
          className="self-start"
        >
          {retrying ? (
            <>
              <RefreshCw size={14} strokeWidth={1.75} className="animate-spin" />
              Retrying…
            </>
          ) : (
            <>
              <RefreshCw size={14} strokeWidth={1.75} />
              Retry processing
            </>
          )}
        </Button>
      )}
    </div>
  );
}

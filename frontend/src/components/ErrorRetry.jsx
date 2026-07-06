import React from 'react';

/**
 * Inline error card with a Retry Processing button. Used inside the
 * document card when a doc's status is `failed`. The Retry button
 * fires `onRetry`; the parent owns the request.
 */
export default function ErrorRetry({ error, onRetry, retrying = false }) {
  return (
    <div className="error-retry" role="alert">
      <div className="error-retry-title">Processing failed</div>
      <div className="error-retry-msg">
        {error || 'An unknown error occurred during ingestion.'}
      </div>
      {onRetry && (
        <button
          className="error-retry-btn"
          onClick={onRetry}
          disabled={retrying}
        >
          {retrying ? 'Retrying…' : 'Retry processing'}
        </button>
      )}
    </div>
  );
}

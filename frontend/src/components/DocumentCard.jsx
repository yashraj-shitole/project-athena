import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import ProgressBar from './ProgressBar.jsx';
import StageChecklist from './StageChecklist.jsx';
import ErrorRetry from './ErrorRetry.jsx';
import { useDocumentEvents } from '../hooks/useDocumentEvents.js';

const STAGES = [
  { key: 'extracting', label: 'Text extraction' },
  { key: 'chunking', label: 'Chunk generation' },
  { key: 'embedding', label: 'Vector embedding' },
  { key: 'indexing', label: 'Database indexing' },
];

function formatBytes(n) {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatDuration(ms) {
  if (ms == null) return '';
  if (ms < 1000) return `${ms} ms`;
  const s = Math.round(ms / 100) / 10;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s - m * 60);
  return `${m}m ${rs}s`;
}

/**
 * The rich document card. Used both in the DocumentManager list
 * (one per doc) and at the top of the detail page. Pulls its live
 * state from the SSE hook.
 *
 * Props:
 *   doc       initial Document row (used for the id and the
 *             synchronous initial state of the SSE hook)
 *   onDelete  optional; if omitted, the Delete button is hidden
 *   onOpen    optional; if omitted, the "Open" link is hidden
 *   showOpen  bool, default true
 */
export default function DocumentCard({ doc, onDelete, onOpen, showOpen = true }) {
  const { state, connected, error, retry, stageLabels } = useDocumentEvents(
    doc.id,
    { initial: doc },
  );
  const [retrying, setRetrying] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const status = state.status || doc.status;
  const isProcessing = status === 'uploaded' || status === 'processing';
  const isFailed = status === 'failed';
  const isIndexed = status === 'indexed';

  // A short, human-friendly status line for the card header.
  const statusLine = useMemo(() => {
    if (isFailed) return 'Failed';
    if (isIndexed) {
      return state.processingTimeMs != null
        ? `Indexed in ${formatDuration(state.processingTimeMs)}`
        : 'Indexed';
    }
    if (isProcessing) {
      const cur = state.currentStage;
      if (cur && stageLabels[cur]) {
        const sp = state.stageProgress || {};
        const pct = sp[cur];
        return pct != null
          ? `${stageLabels[cur]}… ${Math.round(pct)}%`
          : `${stageLabels[cur]}…`;
      }
      return 'Queued for processing';
    }
    return status;
  }, [isFailed, isIndexed, isProcessing, state, status, stageLabels]);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await retry();
    } finally {
      setRetrying(false);
    }
  };

  const handleDelete = async () => {
    if (!onDelete) return;
    setDeleting(true);
    try {
      await onDelete(doc);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="doc-card" data-status={status}>
      <div className="doc-card-header">
        <div className="doc-card-title">
          <div className="doc-card-filename">{state.filename || doc.filename}</div>
          <div className="doc-card-meta">
            {(state.fileType || doc.file_type || '').toUpperCase()} ·{' '}
            {formatBytes(state.sizeBytes ?? doc.size_bytes)}
            {state.pageCount ? ` · ${state.pageCount} page${state.pageCount === 1 ? '' : 's'}` : ''}
            {' · '}
            {formatDate(state.createdAt || doc.created_at)}
          </div>
        </div>
        <div className="doc-card-actions">
          {showOpen && (
            <Link className="doc-card-link" to={`/documents/${doc.id}`}>
              Open
            </Link>
          )}
          {onDelete && (
            <button
              className="danger doc-card-delete"
              onClick={handleDelete}
              disabled={deleting || isProcessing}
              title={isProcessing ? 'Cannot delete while processing' : 'Delete document'}
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          )}
        </div>
      </div>

      <div className="doc-card-status">
        <span className={`status-pill status-${status}`}>{status}</span>
        <span className="doc-card-status-line">{statusLine}</span>
        {!connected && isProcessing && (
          <span className="doc-card-conn" title="Live stream disconnected; reconnecting…">
            (reconnecting)
          </span>
        )}
      </div>

      {isProcessing && (
        <div className="doc-card-progress">
          <ProgressBar
            percent={state.overallPct}
            label={state.overallPct ? undefined : 'Starting…'}
            indeterminate={!state.overallPct}
          />
          <StageChecklist
            stages={STAGES}
            currentStage={state.currentStage}
            stageProgress={state.stageProgress}
            status={status}
          />
        </div>
      )}

      {isFailed && (
        <ErrorRetry
          error={state.errorMessage || doc.error_message}
          onRetry={onDelete || retrying ? undefined : handleRetry}
          retrying={retrying}
        />
      )}

      {isIndexed && (
        <div className="doc-success">
          <div className="doc-success-headline">✓ Processing complete</div>
          <dl className="doc-metadata">
            <dt>Pages</dt>
            <dd>{state.pageCount ?? '—'}</dd>
            <dt>Chunks indexed</dt>
            <dd>{state.chunkCount ?? doc.chunk_count ?? '—'}</dd>
            <dt>Embedding model</dt>
            <dd>{state.embeddingModel || doc.embedding_model || '—'}</dd>
            <dt>Processing time</dt>
            <dd>{formatDuration(state.processingTimeMs ?? doc.processing_time_ms)}</dd>
            <dt>Indexed at</dt>
            <dd>{formatDate(state.processedAt || doc.processed_at)}</dd>
          </dl>
        </div>
      )}

      {error && !isFailed && (
        <div className="doc-card-error" role="status">
          Live stream error: {error}
        </div>
      )}
    </div>
  );
}

import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Trash2, FileText, MoreHorizontal, RefreshCw } from 'lucide-react';
import Card from './ui/Card.jsx';
import Button from './ui/Button.jsx';
import StatusPill from './ui/StatusPill.jsx';
import ProgressBar from './ui/ProgressBar.jsx';
import Badge from './ui/Badge.jsx';
import { Tooltip } from './ui/Tooltip.jsx';
import { DropdownMenu, DropdownItem, DropdownSeparator } from './ui/DropdownMenu.jsx';
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
    <Card data-status={status} className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 flex items-start gap-3">
          <div className="hidden sm:flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-ink-dim">
            <FileText size={16} strokeWidth={1.5} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 min-w-0">
              <p className="font-medium tracking-tight text-ink truncate">
                {state.filename || doc.filename}
              </p>
            </div>
            <p className="text-xs text-ink-dim mt-0.5 truncate">
              {(state.fileType || doc.file_type || '').toUpperCase()} ·{' '}
              {formatBytes(state.sizeBytes ?? doc.size_bytes)}
              {state.pageCount ? ` · ${state.pageCount} page${state.pageCount === 1 ? '' : 's'}` : ''}
              {' · '}
              {formatDate(state.createdAt || doc.created_at)}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {showOpen && (
            <Link to={`/documents/${doc.id}`}>
              <Button variant="ghost" size="sm">Open</Button>
            </Link>
          )}
          {onDelete && (
            <Tooltip content={isProcessing ? 'Cannot delete while processing' : 'Delete document'}>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={handleDelete}
                disabled={deleting || isProcessing}
                aria-label="Delete document"
                className="text-ink-dim hover:text-[var(--danger)]"
              >
                <Trash2 size={15} strokeWidth={1.75} />
              </Button>
            </Tooltip>
          )}
          <DropdownMenu
            trigger={
              <Button variant="ghost" size="icon-sm" aria-label="More actions">
                <MoreHorizontal size={15} strokeWidth={1.75} />
              </Button>
            }
          >
            {showOpen && (
              <DropdownItem asChild>
                <Link to={`/documents/${doc.id}`} className="block w-full">
                  Open
                </Link>
              </DropdownItem>
            )}
            {!isProcessing && !isIndexed && (
              <DropdownItem onSelect={handleRetry} disabled={retrying}>
                <RefreshCw size={14} strokeWidth={1.75} />
                Retry processing
              </DropdownItem>
            )}
            {onDelete && (
              <>
                <DropdownSeparator />
                <DropdownItem danger onSelect={handleDelete} disabled={deleting || isProcessing}>
                  <Trash2 size={14} strokeWidth={1.75} />
                  Delete
                </DropdownItem>
              </>
            )}
          </DropdownMenu>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <StatusPill status={status} />
        <span className="text-sm text-ink-dim">{statusLine}</span>
        {!connected && isProcessing && (
          <Badge tone="warn" size="sm">reconnecting</Badge>
        )}
      </div>

      {isProcessing && (
        <div className="mt-4 flex flex-col gap-3">
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
        <div className="mt-4">
          <ErrorRetry
            error={state.errorMessage || doc.error_message}
            onRetry={onDelete || retrying ? undefined : handleRetry}
            retrying={retrying}
          />
        </div>
      )}

      {isIndexed && (
        <div className="mt-4 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-sm text-[var(--ok)]">
            <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[var(--ok-bg)]">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--ok)]" />
            </span>
            Processing complete
          </div>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 text-sm">
            <dt className="text-ink-dim">Pages</dt>
            <dd className="text-ink tabular-nums">{state.pageCount ?? '—'}</dd>
            <dt className="text-ink-dim">Chunks indexed</dt>
            <dd className="text-ink tabular-nums">{state.chunkCount ?? doc.chunk_count ?? '—'}</dd>
            <dt className="text-ink-dim">Embedding model</dt>
            <dd className="text-ink">{state.embeddingModel || doc.embedding_model || '—'}</dd>
            <dt className="text-ink-dim">Processing time</dt>
            <dd className="text-ink">{formatDuration(state.processingTimeMs ?? doc.processing_time_ms)}</dd>
            <dt className="text-ink-dim">Indexed at</dt>
            <dd className="text-ink">{formatDate(state.processedAt || doc.processed_at)}</dd>
          </dl>
        </div>
      )}

      {error && !isFailed && (
        <div className="mt-3 text-xs text-ink-dim italic" role="status">
          Live stream error: {error}
        </div>
      )}
    </Card>
  );
}

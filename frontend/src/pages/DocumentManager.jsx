import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Upload, FileUp, Search, FileText, AlertCircle, X,
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth.js';
import docService from '../services/docService.js';
import DocumentCard from '../components/DocumentCard.jsx';
import AppShell from '../components/ui/AppShell.jsx';
import Topbar from '../components/ui/Topbar.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import Button from '../components/ui/Button.jsx';
import Select from '../components/ui/Select.jsx';
import Input from '../components/ui/Input.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';
import { Skeleton, SkeletonText } from '../components/ui/Skeleton.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import { useToast } from '../components/ui/Toaster.jsx';
import { fadeUp } from '../components/ui/Motion.jsx';

// Polling cadence. We only poll fast when something is in motion.
// (The per-doc SSE stream drives the in-card progress UI; this list-
// level poll keeps the row list in sync for new uploads and is the
// fallback if a user is on a different replica from the one running
// the pipeline.)
const POLL_FAST_MS = 2000;
const POLL_SLOW_MS = 15000;

// Backend supports these extensions. Keep in sync with the
// FastAPI config (`ATHENA_UPLOAD_ALLOWED_TYPES`).
const ACCEPT = '.csv,.xlsx,.pdf,.doc,.docx,.txt,.md,.html,.htm';
const ACCEPT_LIST = ACCEPT.split(',');

export default function DocumentManager() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const fileRef = useRef(null);
  const [filter, setFilter] = useState('');
  const [search, setSearch] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const toast = useToast();

  // `loadRef` is the single source of truth for the latest fetcher,
  // so the polling interval (which is started once) always calls the
  // current version, not a stale closure from the first render.
  const loadRef = useRef(null);
  // `docsRef` is the latest docs snapshot — read by the polling loop
  // without forcing the effect to re-run on every `setDocs`.
  const docsRef = useRef([]);

  async function load() {
    try {
      const out = await docService.list({ limit: 100 });
      setDocs(out.items);
      setTotal(out.total);
      docsRef.current = out.items;
      setErr(null);
    } catch (e) {
      if (e.aborted) return;
      setErr(e.message);
    } finally {
      // Mark first load complete on success OR failure so the initial
      // "No documents yet" flash (painted before the first fetch resolves)
      // is replaced by a neutral loading state instead of a misleading
      // empty-account message.
      setLoadedOnce(true);
    }
  }
  loadRef.current = load;

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      if (cancelled) return;
      // Skip fetching while the tab is hidden — the line-68 comment promised
      // this savings but the old handler only re-fetched on becoming visible
      // and never stopped the self-scheduling loop, so polling continued in
      // background tabs. Re-schedule slowly and bail until visible again.
      if (!document.hidden) {
        await loadRef.current?.();
        if (cancelled) return;
      }
      // Pick cadence based on whether anything is in flight. Read
      // the latest docs via the ref so we don't depend on the
      // `docs` state (which would re-run this effect on every tick
      // and create an infinite polling loop).
      const anyBusy = docsRef.current.some(
        (d) => d.status === 'uploaded' || d.status === 'processing',
      );
      timer = setTimeout(tick, anyBusy ? POLL_FAST_MS : POLL_SLOW_MS);
    };

    // Kick off immediately, then self-schedule.
    tick();

    // Fire a fresh load when the user comes back to the tab.
    const onVis = () => {
      if (document.visibilityState === 'visible') {
        loadRef.current?.();
      }
    };
    document.addEventListener('visibilitychange', onVis);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Validate a file by extension against the allowed set; we do
  // this client-side so the user gets a clear "not supported" toast
  // before the upload even hits the server.
  function isAccepted(file) {
    const name = (file?.name || '').toLowerCase();
    return ACCEPT_LIST.some((ext) => name.endsWith(ext));
  }

  async function uploadFile(file) {
    if (!file) return;
    if (!isAccepted(file)) {
      toast.show(`"${file.name}" is not a supported file type.`, { tone: 'error' });
      return;
    }
    setUploading(true);
    setErr(null);
    try {
      await docService.upload(file);
      toast.show(`Uploaded "${file.name}".`, { tone: 'success' });
      // Force a refresh so the user sees the new row.
      await load();
    } catch (e2) {
      // Prefer the already-flattened human-readable message. `e2.body.detail`
      // can be a FastAPI validation array of {loc,msg,...} objects, which
      // React cannot render ("Objects are not valid as a React child") — so
      // only fall back to it when it is a plain string.
      const detail = e2?.body?.detail;
      const msg =
        (typeof detail === 'string' ? detail : null) ||
        e2?.message ||
        'upload failed';
      setErr(msg);
      toast.show(msg, { tone: 'error' });
    } finally {
      setUploading(false);
    }
  }

  async function onPick(e) {
    const f = e.target.files?.[0];
    // Reset the input value up front, on every path. Previously this ran only
    // inside `try` after a successful upload, so a failed upload left the
    // input holding the same file — and browsers don't fire `change` for an
    // unchanged value, so the user couldn't re-pick the same file to retry
    // without first choosing a different file or reloading.
    e.target.value = '';
    await uploadFile(f);
  }

  async function onConfirmDelete() {
    if (!pendingDelete) return;
    // `pendingDelete` is the full document object (set in the row's
    // Delete button). Pull out the id; otherwise the request goes to
    // `/api/documents/[object Object]` and 422s.
    const doc = pendingDelete;
    setDeletingId(doc.id);
    try {
      await docService.remove(doc.id);
      // Close the modal only after the delete succeeds. Previously
      // setPendingDelete(null) ran before the await, so the in-flight
      // DELETE gave no feedback, the row's Delete button stayed clickable
      // (allowing a duplicate concurrent delete), and a failure surfaced
      // only as a generic banner with no indication of which file failed.
      setPendingDelete(null);
      toast.show(`Deleted "${doc.filename}".`, { tone: 'success' });
      await load();
    } catch (e) {
      setErr(`Could not delete ${doc.filename}: ${e.message}`);
      toast.show(`Could not delete ${doc.filename}: ${e.message}`, { tone: 'error' });
    } finally {
      setDeletingId(null);
    }
  }

  // Drag-and-drop handlers for the drop zone. We keep these scoped to
  // the drop zone element so accidental drags from the rest of the
  // page don't trigger uploads.
  function onDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!dragActive) setDragActive(true);
  }
  function onDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  }
  async function onDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    const f = e.dataTransfer?.files?.[0];
    await uploadFile(f);
  }

  const visible = (filter ? docs.filter((d) => d.status === filter) : docs)
    .filter((d) => {
      if (!search) return true;
      const q = search.toLowerCase();
      return (d.filename || '').toLowerCase().includes(q);
    });

  return (
    <AppShell>
      <Topbar>
        <div className="flex items-center gap-3">
          <h1 className="text-base font-medium tracking-tight text-ink">Documents</h1>
          <span className="text-xs text-ink-dim tabular-nums">
            {total} total
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative hidden sm:block">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search…"
              className="pl-8 w-56"
            />
          </div>
          <Select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter by status"
            className="w-36"
          >
            <option value="">All statuses</option>
            <option value="uploaded">Uploaded</option>
            <option value="processing">Processing</option>
            <option value="indexed">Indexed</option>
            <option value="failed">Failed</option>
          </Select>
          <Button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
          >
            <Upload size={14} strokeWidth={1.75} />
            {uploading ? 'Uploading…' : 'Upload'}
          </Button>
          <input
            ref={fileRef}
            type="file"
            hidden
            accept={ACCEPT}
            onChange={onPick}
          />
        </div>
      </Topbar>

      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto max-w-4xl px-6 py-8 flex flex-col gap-6">
          <PageHeader
            eyebrow="Library"
            title="Your documents"
            blurb="Upload files to ground Athena's answers. PDFs, spreadsheets, and markdown are chunked, embedded, and indexed automatically."
          />

          {err && (
            <div
              role="alert"
              className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3.5 py-2.5 text-sm text-[var(--danger)] flex items-start gap-2"
            >
              <AlertCircle size={16} strokeWidth={1.75} className="mt-0.5 shrink-0" />
              <span className="flex-1">{err}</span>
              <button
                onClick={() => setErr(null)}
                aria-label="Dismiss"
                className="rounded-md p-0.5 -mt-0.5 hover:bg-[var(--danger-bg)]"
              >
                <X size={14} strokeWidth={1.75} />
              </button>
            </div>
          )}

          <div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            className={`relative rounded-xl border-2 border-dashed transition-colors duration-[var(--motion-base)] ease-out ${
              dragActive
                ? 'border-hairline-strong bg-surface-2'
                : 'border-hairline bg-surface/40'
            }`}
          >
            <div className="flex items-center gap-3 px-5 py-4">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-ink-dim">
                <FileUp size={16} strokeWidth={1.5} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-ink">
                  Drag a file here, or{' '}
                  <button
                    onClick={() => fileRef.current?.click()}
                    className="font-medium underline underline-offset-4 hover:text-ink-dim"
                  >
                    browse
                  </button>
                  .
                </p>
                <p className="text-xs text-ink-dim mt-0.5">
                  CSV, XLSX, PDF, DOC, DOCX, TXT, MD, HTML.
                </p>
              </div>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            {!loadedOnce ? (
              <LoadingList />
            ) : visible.length === 0 ? (
              docs.length === 0 ? (
                <EmptyState
                  icon={FileText}
                  title="No documents yet"
                  description="Upload a CSV, XLSX, PDF, DOC, DOCX, TXT, MD, or HTML file to get started. Your documents will appear here once indexed."
                  primaryAction={
                    <Button onClick={() => fileRef.current?.click()}>
                      <Upload size={14} strokeWidth={1.75} />
                      Upload your first document
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  icon={Search}
                  title="No matches"
                  description={filter
                    ? `No documents with status “${filter}”.`
                    : `No documents match “${search}”.`}
                />
              )
            ) : (
              <AnimatePresence initial={false}>
                {visible.map((d, i) => (
                  <motion.div
                    key={d.id}
                    layout
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0, transition: { delay: i * 0.02 } }}
                    exit={{ opacity: 0, y: -6 }}
                  >
                    <DocumentCard
                      doc={d}
                      onDelete={(docArg) => setPendingDelete(docArg)}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>
            )}
          </div>
        </div>
      </div>

      <Dialog
        open={!!pendingDelete}
        onOpenChange={(open) => {
          if (!open && !deletingId) setPendingDelete(null);
        }}
        size="sm"
        title="Delete document?"
        description={
          <>
            <strong className="font-medium text-ink">{pendingDelete?.filename}</strong>{' '}
            and all of its indexed chunks will be permanently removed.
          </>
        }
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setPendingDelete(null)}
              disabled={!!deletingId}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={onConfirmDelete}
              disabled={!!deletingId}
            >
              {deletingId ? 'Deleting…' : 'Delete'}
            </Button>
          </>
        }
      />
    </AppShell>
  );
}

function LoadingList() {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="rounded-xl border border-hairline bg-surface p-5 flex flex-col gap-3"
        >
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-9 rounded-lg" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-3 w-48" />
              <Skeleton className="h-2.5 w-32" />
            </div>
          </div>
          <SkeletonText lines={2} />
        </div>
      ))}
    </div>
  );
}

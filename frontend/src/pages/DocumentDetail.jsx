import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ArrowLeft, FileText, AlertCircle, ChevronRight } from 'lucide-react';
import docService from '../services/docService.js';
import DocumentCard from '../components/DocumentCard.jsx';
import AppShell from '../components/ui/AppShell.jsx';
import Topbar from '../components/ui/Topbar.jsx';
import Button from '../components/ui/Button.jsx';
import Card from '../components/ui/Card.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import { Skeleton } from '../components/ui/Skeleton.jsx';
import { useToast } from '../components/ui/Toaster.jsx';
import { fadeUp } from '../components/ui/Motion.jsx';

/**
 * /documents/:id — the document detail page. Renders the same
 * DocumentCard as the list (so progress, retry, and the success
 * block behave identically), followed by the chunk list so the user
 * can see what was actually indexed.
 */
export default function DocumentDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const [doc, setDoc] = useState(null);
  const [chunks, setChunks] = useState(null);
  const [err, setErr] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const d = await docService.get(id);
        if (!cancelled) setDoc(d);
        try {
          const c = await docService.chunks(id);
          if (!cancelled) setChunks(c);
        } catch (e2) {
          // Chunks failing isn't fatal — show the doc without them.
          if (!cancelled) setChunks([]);
        }
      } catch (e) {
        if (!cancelled) setErr(e.message || 'Failed to load document');
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleDelete = async () => {
    try {
      await docService.remove(id);
      toast.show('Document deleted.', { tone: 'success' });
      nav('/');
    } catch (e) {
      setErr(e.message || 'Delete failed');
      setConfirmingDelete(false);
      toast.show(e.message || 'Delete failed', { tone: 'error' });
    }
  };

  return (
    <AppShell>
      <Topbar>
        <div className="flex items-center gap-2 text-sm">
          <Link to="/" className="text-ink-dim hover:text-ink transition-colors">
            Documents
          </Link>
          <ChevronRight size={14} strokeWidth={1.75} className="text-ink-faint" />
          <span className="text-ink font-medium truncate max-w-[280px]">
            {doc?.filename || '…'}
          </span>
        </div>
        <Button variant="ghost" onClick={() => nav(-1)}>
          <ArrowLeft size={14} strokeWidth={1.75} />
          Back
        </Button>
      </Topbar>

      <div className="flex-1 min-h-0 overflow-y-auto">
        <motion.div
          variants={fadeUp}
          initial="hidden"
          animate="show"
          className="mx-auto max-w-3xl px-6 py-8 flex flex-col gap-6"
        >
          {err && (
            <div
              role="alert"
              className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3.5 py-2.5 text-sm text-[var(--danger)] flex items-start gap-2"
            >
              <AlertCircle size={16} strokeWidth={1.75} className="mt-0.5 shrink-0" />
              <span className="flex-1">{err}</span>
            </div>
          )}

          {!doc && !err && (
            <Card className="p-5">
              <div className="flex items-center gap-3">
                <Skeleton className="h-9 w-9 rounded-lg" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-3 w-48" />
                  <Skeleton className="h-2.5 w-32" />
                </div>
              </div>
              <div className="mt-4 space-y-2">
                <Skeleton className="h-2 w-full" />
                <Skeleton className="h-2 w-5/6" />
                <Skeleton className="h-2 w-2/3" />
              </div>
            </Card>
          )}

          {doc && (
            <>
              <DocumentCard
                doc={doc}
                showOpen={false}
                onDelete={() => setConfirmingDelete(true)}
              />

              <Card className="p-5">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-base font-medium tracking-tight text-ink">Indexed chunks</h2>
                    <p className="text-xs text-ink-dim mt-0.5">
                      The pieces the retriever will surface in chat.
                    </p>
                  </div>
                  <span className="text-xs text-ink-dim tabular-nums">
                    {chunks?.length ?? '—'} chunks
                  </span>
                </div>
                {chunks == null ? (
                  <div className="space-y-2">
                    <Skeleton className="h-2 w-full" />
                    <Skeleton className="h-2 w-5/6" />
                    <Skeleton className="h-2 w-2/3" />
                  </div>
                ) : chunks.length === 0 ? (
                  <p className="text-sm text-ink-dim">
                    No chunks yet. Once indexing finishes, the chunk
                    contents will appear here.
                  </p>
                ) : (
                  <ol className="flex flex-col gap-3">
                    {chunks.map((c) => (
                      <li
                        key={c.id}
                        className="rounded-lg border border-hairline bg-surface-2/30 p-3.5"
                      >
                        <div className="flex items-center gap-2 text-[11px] text-ink-faint uppercase tracking-wider mb-1.5">
                          <FileText size={11} strokeWidth={1.75} />
                          Chunk #{c.chunk_index}
                          {c.page_number ? ` · page ${c.page_number}` : ''}
                        </div>
                        <p className="text-sm text-ink leading-relaxed whitespace-pre-wrap">
                          {c.content}
                        </p>
                      </li>
                    ))}
                  </ol>
                )}
              </Card>
            </>
          )}
        </motion.div>
      </div>

      <Dialog
        open={confirmingDelete}
        onOpenChange={setConfirmingDelete}
        size="sm"
        title="Delete document?"
        description={
          <>
            <strong className="font-medium text-ink">{doc?.filename}</strong>{' '}
            and all of its indexed chunks will be permanently removed.
          </>
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={handleDelete}>
              Delete
            </Button>
          </>
        }
      />
    </AppShell>
  );
}

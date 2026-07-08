import React, { createContext, useCallback, useContext, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X, CheckCircle2, AlertCircle, Info, AlertTriangle } from 'lucide-react';
import { cn } from '../../lib/cn.js';

/**
 * Toaster — a tiny promise-free toast queue. Components call
 * `useToast().show('Saved')` to surface a message; the queue renders
 * them stacked at the bottom-right of the viewport. We don't pull
 * in a toast library for a four-icon, four-tone surface.
 */
const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const show = useCallback((message, opts = {}) => {
    const id = Math.random().toString(36).slice(2, 9);
    const t = {
      id,
      message,
      tone: opts.tone || 'info',
      duration: opts.duration ?? 4000,
    };
    setToasts((q) => [...q, t]);
    if (t.duration > 0) {
      setTimeout(() => {
        setToasts((q) => q.filter((x) => x.id !== id));
      }, t.duration);
    }
    return id;
  }, []);

  const dismiss = useCallback((id) => {
    setToasts((q) => q.filter((x) => x.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ show, dismiss }}>
      {children}
      <div className="pointer-events-none fixed bottom-6 right-6 z-[100] flex w-full max-w-sm flex-col items-end gap-2">
        <AnimatePresence>
          {toasts.map((t) => (
            <Toast key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

function Toast({ toast, onDismiss }) {
  const Icon =
    toast.tone === 'success' ? CheckCircle2 :
    toast.tone === 'error'   ? AlertCircle :
    toast.tone === 'warn'    ? AlertTriangle :
    Info;
  const toneClass =
    toast.tone === 'success' ? 'text-[var(--ok)]' :
    toast.tone === 'error'   ? 'text-[var(--danger)]' :
    toast.tone === 'warn'    ? 'text-[var(--warn)]' :
    'text-[var(--info)]';
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 12, scale: 0.98 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        'pointer-events-auto w-full rounded-lg border border-hairline bg-surface',
        'shadow-floating px-3.5 py-3',
        'flex items-start gap-3',
      )}
      role="status"
    >
      <Icon size={18} className={cn('mt-0.5 shrink-0', toneClass)} strokeWidth={1.75} />
      <p className="flex-1 text-sm leading-relaxed text-ink">{toast.message}</p>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="rounded-md p-0.5 text-ink-faint hover:text-ink hover:bg-surface-2 transition-colors"
      >
        <X size={14} strokeWidth={1.75} />
      </button>
    </motion.div>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>');
  return ctx;
}

export default ToastProvider;

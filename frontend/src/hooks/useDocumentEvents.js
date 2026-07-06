import { useCallback, useEffect, useRef, useState } from 'react';
import docService from '../services/docService.js';

// Weight per stage, matching the server's `overall_pct` formula. We
// keep it here too as a fallback so a STATE event without
// `overall_pct` (older backends) still renders a sensible bar.
const STAGE_WEIGHTS = {
  uploading: 5,
  extracting: 20,
  chunking: 15,
  embedding: 40,
  indexing: 20,
};

const STAGE_ORDER = ['uploading', 'extracting', 'chunking', 'embedding', 'indexing'];

const STAGE_LABELS = {
  uploading: 'Uploading',
  extracting: 'Text extraction',
  chunking: 'Chunk generation',
  embedding: 'Vector embedding',
  indexing: 'Database indexing',
};

function computeOverallPct(currentStage, stageProgress) {
  if (!currentStage) return 0;
  const sp = stageProgress || {};
  const totalWeight = Object.values(STAGE_WEIGHTS).reduce((a, b) => a + b, 0) || 1;
  let pct = 0;
  const currentIdx = STAGE_ORDER.indexOf(currentStage);
  for (let i = 0; i < STAGE_ORDER.length; i++) {
    const st = STAGE_ORDER[i];
    const weight = STAGE_WEIGHTS[st] || 0;
    if (i < currentIdx) {
      pct += weight;
    } else if (i === currentIdx) {
      const p = Math.max(0, Math.min(100, sp[st] || 0));
      pct += Math.round((weight * p) / 100);
      break;
    } else {
      break;
    }
  }
  return Math.max(0, Math.min(100, Math.round((pct * 100) / totalWeight)));
}

function applyEvent(state, evt) {
  switch (evt.type) {
    case 'STATE': {
      // Full row mirror from the server. The server's `overall_pct`
      // is authoritative; if it's missing (older backend) we fall
      // back to the local computation.
      const stageProgress = evt.stage_progress || {};
      return {
        ...state,
        status: evt.status,
        currentStage: evt.current_stage,
        stageProgress,
        overallPct: typeof evt.overall_pct === 'number'
          ? evt.overall_pct
          : computeOverallPct(evt.current_stage, stageProgress),
        chunkCount: evt.chunk_count,
        pageCount: evt.page_count,
        embeddingModel: evt.embedding_model,
        errorMessage: evt.error_message,
        startedAt: evt.started_at,
        processedAt: evt.processed_at,
        processingTimeMs: evt.processing_time_ms,
        // Identity fields for the UI's header.
        filename: evt.filename,
        fileType: evt.file_type,
        sizeBytes: evt.size_bytes,
        createdAt: evt.created_at,
      };
    }
    case 'STAGE': {
      // Stage transition. Merge so we don't drop other progress.
      const nextProgress = { ...(state.stageProgress || {}) };
      if (evt.to_stage) {
        // If we're entering a new stage, mark the previous as 100% if
        // it isn't already (e.g. jumping straight from "extracting" to
        // "embedding" without an explicit 100% on extracting).
        const currentIdx = STAGE_ORDER.indexOf(evt.to_stage);
        for (let i = 0; i < currentIdx; i++) {
          if (nextProgress[STAGE_ORDER[i]] == null) {
            nextProgress[STAGE_ORDER[i]] = 100;
          }
        }
        if (nextProgress[evt.to_stage] == null) {
          nextProgress[evt.to_stage] = 0;
        }
      }
      return {
        ...state,
        currentStage: evt.to_stage,
        stageProgress: nextProgress,
        overallPct: computeOverallPct(evt.to_stage, nextProgress),
      };
    }
    case 'PROGRESS': {
      const stage = evt.stage;
      const pct = typeof evt.percent === 'number' ? evt.percent : null;
      if (!stage || pct == null) return state;
      const nextProgress = { ...(state.stageProgress || {}), [stage]: pct };
      return {
        ...state,
        currentStage: stage,
        stageProgress: nextProgress,
        overallPct: computeOverallPct(stage, nextProgress),
      };
    }
    case 'TERMINAL': {
      const nextProgress = { ...(state.stageProgress || {}) };
      // Mark everything as 100% on success, leave alone on failure.
      if (evt.status === 'indexed') {
        for (const s of STAGE_ORDER) {
          if (nextProgress[s] == null) nextProgress[s] = 100;
        }
      }
      return {
        ...state,
        status: evt.status,
        errorMessage: evt.error_message || state.errorMessage,
        chunkCount: evt.chunk_count ?? state.chunkCount,
        embeddingModel: evt.embedding_model ?? state.embeddingModel,
        processingTimeMs: evt.processing_time_ms ?? state.processingTimeMs,
        stageProgress: nextProgress,
        currentStage: evt.status === 'indexed' ? 'completed' : 'failed',
        overallPct: evt.status === 'indexed' ? 100 : state.overallPct,
        connected: false,
      };
    }
    default:
      return state;
  }
}

/**
 * Subscribe to a document's SSE status stream and expose a derived
 * view model for the UI.
 *
 *   const { state, retry, connected, error } = useDocumentEvents(id, { initial });
 *
 * - `state` mirrors what the row in the DB will look like once the
 *   pipeline finishes — page count, chunk count, embedding model,
 *   processing time, etc. — so the UI can render the same card on
 *   the list page and the detail page.
 * - `connected` is true while the SSE stream is live.
 * - `error` is a human-readable error message, or null.
 * - `retry()` triggers a server-side re-ingest; the SSE stream stays
 *   open and emits fresh events.
 */
export function useDocumentEvents(documentId, options = {}) {
  const { initial = null } = options;

  // Seed state from a synchronous initial payload if provided (e.g. a
  // GET that returned before the SSE connected). Otherwise start
  // empty — the first STATE event fills it in.
  const [state, setState] = useState(() => {
    if (initial) {
      return applyEvent(
        {
          status: initial.status,
          currentStage: initial.current_stage,
          stageProgress: initial.stage_progress || {},
          overallPct: 0,
          chunkCount: initial.chunk_count,
          pageCount: initial.page_count,
          embeddingModel: initial.embedding_model,
          errorMessage: initial.error_message,
          startedAt: initial.started_at,
          processedAt: initial.processed_at,
          processingTimeMs: initial.processing_time_ms,
          filename: initial.filename,
          fileType: initial.file_type,
          sizeBytes: initial.size_bytes,
          createdAt: initial.created_at,
        },
        { type: 'STATE', ...initial, overall_pct: undefined },
      );
    }
    return {
      status: null,
      currentStage: null,
      stageProgress: {},
      overallPct: 0,
      chunkCount: null,
      pageCount: null,
      embeddingModel: null,
      errorMessage: null,
      startedAt: null,
      processedAt: null,
      processingTimeMs: null,
      filename: null,
      fileType: null,
      sizeBytes: null,
      createdAt: null,
    };
  });
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);

  // Refs to allow the reconnect timer / abort controller to be
  // torn down on unmount without re-running the effect.
  const abortRef = useRef(null);
  const attemptRef = useRef(0);
  const stoppedRef = useRef(false);
  const reconnectTimerRef = useRef(null);

  const start = useCallback(async () => {
    if (stoppedRef.current) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let resp;
    try {
      resp = await docService.eventsStream(documentId, { signal: ctrl.signal });
    } catch (e) {
      if (e?.aborted || e?.name === 'AbortError') return;
      setError(e?.message || 'Failed to connect');
      return;
    }
    if (!resp.body) {
      setError('No response body');
      return;
    }
    setConnected(true);
    setError(null);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    // Same splitBlocks as useChatStream: handles \n\n and \r\n\r\n.
    const splitBlocks = (s) => {
      const out = [];
      let i = 0;
      while (i < s.length) {
        const a = s.indexOf('\n\n', i);
        const b = s.indexOf('\r\n\r\n', i);
        let next = -1;
        let width = 0;
        if (a >= 0 && (b < 0 || a <= b)) { next = a; width = 2; }
        else if (b >= 0) { next = b; width = 4; }
        if (next < 0) break;
        out.push(s.slice(i, next));
        i = next + width;
      }
      return [out, s.slice(i)];
    };
    try {
      while (true) {
        const { value, done: rdone } = await reader.read();
        if (rdone) break;
        if (ctrl.signal.aborted) break;
        buf += decoder.decode(value, { stream: true });
        const [blocks, rest] = splitBlocks(buf);
        buf = rest;
        for (const raw of blocks) {
          const block = raw.trim();
          if (!block.startsWith('data:')) continue;
          const payload = block.slice(5).trim();
          if (!payload) continue;
          let evt;
          try {
            evt = JSON.parse(payload);
          } catch {
            continue;
          }
          // Update synchronously so the UI sees a single render per
          // event, not one per reducer pass.
          setState((prev) => applyEvent(prev, evt));
          // Reset backoff after a successful event.
          attemptRef.current = 0;
          if (evt.type === 'TERMINAL') {
            // The server closes the stream right after this; the
            // reader will see `done` on the next read. Mark
            // disconnected now so the UI clears any spinner.
            setConnected(false);
            return;
          }
        }
      }
    } catch (e) {
      if (e?.name === 'AbortError') return;
      setError(e?.message || 'Stream error');
    } finally {
      setConnected(false);
    }
    // The stream ended without a TERMINAL (network drop, server
    // restart). Schedule a backoff reconnect, but only if the doc
    // hasn't reached a terminal status — reconnecting to an
    // `indexed` doc just gets a fresh STATE and a closed socket.
    setState((prev) => {
      if (prev.status === 'indexed' || prev.status === 'failed') {
        return prev;
      }
      const attempt = attemptRef.current + 1;
      attemptRef.current = attempt;
      const delayMs = Math.min(5000, 500 * 2 ** Math.min(attempt, 5));
      reconnectTimerRef.current = setTimeout(() => {
        start();
      }, delayMs);
      return prev;
    });
  }, [documentId]);

  // Initial connect + cleanup on unmount or id change.
  useEffect(() => {
    stoppedRef.current = false;
    start();
    return () => {
      stoppedRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, [start]);

  // Public API: trigger a server-side retry. The hook re-seeds the
  // state with the row returned by the server (status='processing')
  // and reconnects the SSE stream so the next STATE reflects the
  // fresh attempt.
  const retry = useCallback(async () => {
    try {
      const doc = await docService.retry(documentId);
      setError(null);
      setState(applyEvent(state, { type: 'STATE', ...doc, overall_pct: 0 }));
      // Force a fresh SSE cycle: the existing one is still open and
      // will get a STAGE event from the retry's STAGE publish, but a
      // reconnect makes the UI behavior more predictable.
      if (abortRef.current) abortRef.current.abort();
      attemptRef.current = 0;
      await start();
    } catch (e) {
      setError(e?.message || 'Retry failed');
    }
  }, [documentId, start, state]);

  return {
    state,
    connected,
    error,
    retry,
    stageLabels: STAGE_LABELS,
    stageOrder: STAGE_ORDER,
  };
}

export default useDocumentEvents;

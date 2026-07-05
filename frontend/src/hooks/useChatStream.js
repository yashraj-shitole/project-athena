import { useCallback, useRef, useState } from 'react';
import apiClient from '../services/apiClient.js';

/**
 * Stream a chat turn over SSE. Returns:
 *   - text: streamed assistant text (so far)
 *   - citations / usedTools / toolEvents from the run
 *   - runId, done, error
 *   - send(content, opts)
 *   - reset()
 *   - cancel()
 *
 * The backend emits AG-UI-shaped events:
 *   RUN_STARTED, TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT, TOOL_CALL_*,
 *   TEXT_MESSAGE_END, RUN_FINISHED, RUN_ERROR
 *
 * Lifecycle gotchas (fixed in this revision):
 *   - The `send` callback previously had `reset` in its deps array, but
 *     `reset` is itself a fresh `useCallback` whose only dep is `[]`,
 *     so it was stable — but we removed it from the deps anyway to
 *     avoid being re-created for any reason and causing the consumer
 *     to re-render / re-send in a loop.
 *   - `done` and `runId` are now reset in `send` before we begin
 *     (so the streaming bubble hides immediately on next send).
 *   - If the SSE stream ends without a RUN_FINISHED / RUN_ERROR
 *     event (network drop, mid-stream cancellation), we still
 *     mark `done=true` after the reader loop so the UI can clean up.
 *   - `cancel()` aborts the underlying fetch via AbortController.
 */
export function useChatStream() {
  const [text, setText] = useState('');
  const [citations, setCitations] = useState([]);
  const [usedTools, setUsedTools] = useState([]);
  const [runId, setRunId] = useState(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState(null);
  const [toolEvents, setToolEvents] = useState([]);

  const abortRef = useRef(null);
  // Track whether a run is in flight so the consumer can disable Send
  // and we can early-exit a second click.
  const inFlightRef = useRef(false);

  const reset = useCallback(() => {
    setText('');
    setCitations([]);
    setUsedTools([]);
    setRunId(null);
    setDone(false);
    setError(null);
    setToolEvents([]);
  }, []);

  const cancel = useCallback(() => {
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch (_) { /* noop */ }
      abortRef.current = null;
    }
    setDone(true);
  }, []);

  const send = useCallback(async (message, opts = {}) => {
    if (inFlightRef.current) {
      // Double-send protection: ignore a second click while a stream
      // is still in flight.
      return;
    }
    inFlightRef.current = true;
    reset();

    // Fresh AbortController per run so cancel() works mid-stream.
    const ac = new AbortController();
    abortRef.current = ac;

    let resp;
    try {
      resp = await apiClient.stream(
        '/chat/stream',
        {
          message,
          conversation_id: opts.conversationId || null,
          tool_subset: opts.toolSubset || null,
          stream: true,
        },
        { signal: ac.signal },
      );
    } catch (e) {
      if (e.name === 'AbortError') {
        setError('cancelled');
      } else {
        setError(e.message || String(e));
      }
      setDone(true);
      inFlightRef.current = false;
      abortRef.current = null;
      return;
    }

    if (!resp.ok || !resp.body) {
      setError(`stream_error: ${resp.status}`);
      setDone(true);
      inFlightRef.current = false;
      abortRef.current = null;
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    // Accept both \n\n (RFC) and \r\n\r\n (some proxies) as event
    // boundaries.
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
        if (ac.signal.aborted) break;
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
          switch (evt.type) {
            case 'RUN_STARTED':
              setRunId(evt.run_id);
              break;
            case 'TEXT_MESSAGE_START':
              setText('');
              break;
            case 'TEXT_MESSAGE_CONTENT':
              setText((t) => t + (evt.delta || ''));
              break;
            case 'TEXT_MESSAGE_END':
              setCitations(evt.citations || []);
              setUsedTools(evt.used_tools || []);
              break;
            case 'TOOL_CALL_START':
              setToolEvents((t) => [
                ...t,
                { name: evt.tool_name, status: 'started' },
              ]);
              break;
            case 'TOOL_CALL_END':
              setToolEvents((t) => {
                const next = [...t];
                const i = next.findIndex(
                  (e) => e.name === evt.tool_name && e.status === 'started',
                );
                if (i >= 0) next[i] = { name: evt.tool_name, status: evt.status };
                else next.push({ name: evt.tool_name, status: evt.status });
                return next;
              });
              break;
            case 'RUN_FINISHED':
              setDone(true);
              break;
            case 'RUN_ERROR':
              setError(evt.error || 'run_error');
              setDone(true);
              break;
            default:
              // ignore unknown events
              break;
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        setError('cancelled');
      } else {
        setError(e.message || String(e));
      }
    } finally {
      // Mark done even if the stream was cut off without RUN_FINISHED.
      setDone(true);
      // Clear runId so the stream-bubble disappears once the run
      // completes — otherwise the persisted assistant message (added
      // via refreshActive) would render alongside the live
      // stream-bubble and show the same text twice.
      setRunId(null);
      inFlightRef.current = false;
      abortRef.current = null;
      // Release the reader so the underlying socket is freed.
      try { reader.releaseLock(); } catch (_) { /* noop */ }
    }
  }, [reset]);

  return { text, citations, usedTools, runId, done, error, toolEvents, send, reset, cancel };
}

export default useChatStream;

/**
 * Tiny fetch wrapper that:
 *   - prepends /api
 *   - attaches the JWT (if any)
 *   - parses JSON or text
 *   - surfaces 401 by clearing the token + emitting `athena:auth-failed`
 *     so the React layer can redirect via the router (preserves state)
 */
const BASE = '/api';

// 30s default for normal requests, no timeout for streams (caller-managed).
const DEFAULT_TIMEOUT_MS = 30_000;

export const AUTH_EVENT = 'athena:auth-failed';

function getToken() {
  return localStorage.getItem('athena_token');
}
export { getToken };

export function setToken(token) {
  if (token) localStorage.setItem('athena_token', token);
  else localStorage.removeItem('athena_token');
}

export function setRefreshToken(token) {
  if (token) localStorage.setItem('athena_refresh', token);
  else localStorage.removeItem('athena_refresh');
}

export function setTokens(access, refresh) {
  setToken(access);
  setRefreshToken(refresh);
}

export function getRefreshToken() {
  return localStorage.getItem('athena_refresh');
}

export function clearTokens() {
  localStorage.removeItem('athena_token');
  localStorage.removeItem('athena_refresh');
}

// A refresh token is stored on login/register but was never redeemed — every
// access-token expiry bounced the user to /login and discarded in-memory
// chat/document state. `_tryRefresh` redeems the refresh token once; concurrent
// 401s coalesce onto the same in-flight promise so we don't fire N parallel
// /auth/refresh calls. It uses a raw fetch (not apiClient.post) so a 401 from
// the refresh endpoint itself can never recurse back into _handle401.
let _refreshing = null;
async function _tryRefresh() {
  const rt = getRefreshToken();
  if (!rt) return false;
  if (_refreshing) return _refreshing;
  _refreshing = (async () => {
    try {
      const res = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) return false;
      const data = await res.json().catch(() => null);
      if (!data || !data.access_token) return false;
      setTokens(data.access_token, data.refresh_token || rt);
      return true;
    } catch {
      return false;
    } finally {
      _refreshing = null;
    }
  })();
  return _refreshing;
}

/**
 * Handle a 401. Returns true if the access token was silently refreshed and
 * the caller should retry the original request once; false if the session is
 * terminal (tokens cleared + AUTH_EVENT dispatched so the React layer
 * redirects). The refresh attempt runs first, so a valid refresh token no
 * longer forces a re-login.
 */
async function _handle401(reason) {
  const refreshed = await _tryRefresh();
  if (refreshed) return true;
  clearTokens();
  // Tell the app to redirect — listeners (e.g. a top-level <AuthBoundary/>)
  // can use the router and preserve location state.
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(AUTH_EVENT, { detail: { reason } }));
  }
  // Hard fallback only if no listener redirected within a tick.
  // (This is the safety net; the React layer should beat us to it.)
  setTimeout(() => {
    if (location.pathname !== '/login' && !location.search.includes('next=')) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = `/login?next=${next}`;
    }
  }, 0);
  return false;
}

async function _readError(res) {
  let body = null;
  const ct = res.headers.get('content-type') || '';
  try {
    body = ct.includes('application/json') ? await res.json() : await res.text();
  } catch {
    body = res.statusText;
  }
  let message;
  if (body && typeof body === 'object') {
    const detail = body.detail;
    if (typeof detail === 'string') {
      message = detail;
    } else if (Array.isArray(detail)) {
      // FastAPI validation error: detail is a list of {type, loc, msg, ...}
      // entries. Render them as a human-readable summary.
      message = detail
        .map((e) => {
          if (!e || typeof e !== 'object') return String(e);
          const where = Array.isArray(e.loc) ? e.loc.join('.') : '';
          return where ? `${where}: ${e.msg || e.type || 'invalid'}` : (e.msg || e.type || 'invalid');
        })
        .join('; ');
    } else if (detail && typeof detail === 'object') {
      message = detail.message || detail.detail || res.statusText;
    } else {
      message = body.message || res.statusText;
    }
  } else {
    message = body || res.statusText;
  }
  const err = new Error(message || `HTTP ${res.status}`);
  err.status = res.status;
  err.body = body;
  return err;
}

function _timeoutSignal(ms) {
  const ctrl = new AbortController();
  // Hold the timer id so the caller can clear it once the fetch settles.
  // Without clearing, the timer (and the AbortController it closes over)
  // lives until the full timeout elapses even on a fast 200 — a leak that
  // adds up across many requests.
  const timer = setTimeout(() => ctrl.abort(), ms);
  return { signal: ctrl.signal, clear: () => clearTimeout(timer) };
}

async function request(path, opts = {}) {
  const baseHeaders = { ...(opts.headers || {}) };
  // Only set Content-Type when there's a JSON body. Setting it on a bodyless
  // GET/DELETE is harmless in spec but some proxies/load-balancers will
  // strip the request and reject. Multipart uploads leave it unset
  // so the browser fills in the boundary.
  const hasJsonBody = opts.body !== undefined && opts.body !== null;
  if (hasJsonBody) baseHeaders['Content-Type'] = baseHeaders['Content-Type'] || 'application/json';

  // Single fetch attempt. Reads the token fresh each call so a retry after
  // refresh picks up the new access token. Each call owns (and clears) its
  // own timeout signal.
  async function attempt() {
    const headers = { ...baseHeaders };
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const owned = opts.signal ? null : _timeoutSignal(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    const signal = opts.signal || owned.signal;
    try {
      return await fetch(`${BASE}${path}`, { ...opts, headers, signal });
    } catch (e) {
      if (e.name === 'AbortError') {
        const err = new Error('Request cancelled');
        err.status = 0;
        err.aborted = true;
        throw err;
      }
      // Network / DNS / CORS — surface a friendly message.
      const err = new Error('Cannot reach the server. Check your connection.');
      err.status = 0;
      err.cause = e;
      throw err;
    } finally {
      if (owned) owned.clear();
    }
  }

  let res = await attempt();
  if (res.status === 401) {
    // Try to redeem the refresh token; if it succeeds, retry once.
    const refreshed = await _handle401('request');
    if (refreshed) res = await attempt();
    if (res.status === 401) {
      const err = new Error('unauthorized');
      err.status = 401;
      throw err;
    }
  }
  if (!res.ok) {
    throw await _readError(res);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export const apiClient = {
  get: (p, opts) => request(p, { method: 'GET', ...(opts || {}) }),
  post: (p, body, opts) => request(p, { method: 'POST', body: JSON.stringify(body), ...(opts || {}) }),
  patch: (p, body, opts) => request(p, { method: 'PATCH', body: JSON.stringify(body), ...(opts || {}) }),
  put: (p, body, opts) => request(p, { method: 'PUT', body: JSON.stringify(body), ...(opts || {}) }),
  del: (p, opts) => request(p, { method: 'DELETE', ...(opts || {}) }),

  /**
   * Upload a file using multipart/form-data.
   * Content-Type is left unset so the browser sets the boundary.
   */
  async upload(path, formData, opts = {}) {
    async function attempt() {
      const headers = { ...(opts.headers || {}) };
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      const owned = opts.signal ? null : _timeoutSignal(opts.timeoutMs ?? 5 * 60_000);
      const signal = opts.signal || owned.signal;
      try {
        return await fetch(`${BASE}${path}`, { method: 'POST', body: formData, headers, signal });
      } catch (e) {
        if (e.name === 'AbortError') {
          const err = new Error('Upload cancelled');
          err.status = 0;
          err.aborted = true;
          throw err;
        }
        const err = new Error('Upload failed: cannot reach the server.');
        err.status = 0;
        err.cause = e;
        throw err;
      } finally {
        if (owned) owned.clear();
      }
    }
    let res = await attempt();
    if (res.status === 401) {
      const refreshed = await _handle401('upload');
      if (refreshed) res = await attempt();
      if (res.status === 401) throw new Error('unauthorized');
    }
    if (!res.ok) {
      const err = await _readError(res);
      throw err;
    }
    return res.json();
  },

  /**
   * Open a streaming POST. Returns the raw Response so the caller can
   * iterate over SSE events. Caller is responsible for cancellation
   * via the `signal` option.
   *
   * Unlike a normal request, the SSE consumer reads `res.body` directly,
   * so a 401 here would otherwise surface as a malformed-stream parse
   * error. Detect 401 up front and route it through the same auth-failed
   * path as `request()`.
   */
  async stream(path, body, opts = {}) {
    const attempt = () => {
      const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      return fetch(`${BASE}${path}`, {
        method: 'POST',
        body: JSON.stringify(body),
        headers,
        signal: opts.signal,
      });
    };
    let res = await attempt();
    if (res.status === 401) {
      // Drain so the connection can be reused, then route to auth-failed.
      try { await res.text(); } catch { /* ignore */ }
      const refreshed = await _handle401('stream');
      if (refreshed) res = await attempt();
      if (res.status === 401) {
        try { await res.text(); } catch { /* ignore */ }
        const err = new Error('unauthorized');
        err.status = 401;
        throw err;
      }
    }
    if (!res.ok) {
      // Surface non-SSE error bodies (e.g. 413/500) as readable errors.
      const err = await _readError(res).catch(() => new Error(`HTTP ${res.status}`));
      throw err;
    }
    return res;
  },
};

export default apiClient;

import { useEffect, useSyncExternalStore } from 'react';
import { getToken, setTokens, clearTokens, AUTH_EVENT } from '../services/apiClient.js';
import authService from '../services/authService.js';

/**
 * Auth store (singleton). Exposes a `subscribe` for `useSyncExternalStore`
 * so every consumer re-renders the moment tokens change — including
 * after login() resolves.
 *
 * Why a singleton and not a per-component hook? Because the previous
 * version re-ran its bootstrap effect on every mount, issued a /me
 * request, and the consumer's `token` was a fresh local-variable read
 * from localStorage on each render (not state). That broke:
 *   - Login → navigate to /chat → Protected re-mounts and re-bootstraps.
 *   - Logout → some components held a stale `token` from their last
 *     render and kept showing protected content for a frame.
 * The singleton flips the whole tree atomically.
 */

let _state = {
  token: getToken(),
  user: null,
  ready: false,
  bootstrapping: false,
};

const listeners = new Set();
function emit() {
  for (const fn of listeners) fn();
}

function setState(patch) {
  _state = { ..._state, ...patch };
  emit();
}

/**
 * Keep the singleton in sync with `athena:auth-failed`.
 *
 * apiClient._handle401 clears localStorage and dispatches AUTH_EVENT when a
 * request comes back 401. Without this listener the singleton's `_state.token`
 * stays stale (it is only mutated by login/register/logout/refresh/bootstrap),
 * so localStorage and the singleton diverge. That divergence caused an
 * infinite redirect loop: AuthBoundary navs to /login?next=/chat, Login's
 * `navigate when ready && token` effect trusts the stale singleton token and
 * navs straight back to /chat, Protected renders ChatInterface, it fires
 * loadConversations -> 401 -> repeat. The user is locked out of the login
 * form entirely.
 *
 * Registering at module load (useAuth is imported transitively before
 * AuthBoundary mounts) means this handler runs synchronously during
 * dispatchEvent, before AuthBoundary's listener, flipping token to null so
 * Login's effect no-ops and Protected redirects instead of rendering. The
 * `if (_state.token)` guard avoids a redundant emit when bootstrap-logout
 * already cleared it. No circular import: apiClient does not import useAuth.
 */
if (typeof window !== 'undefined') {
  window.addEventListener(AUTH_EVENT, () => {
    if (_state.token) setState({ token: null, user: null, ready: true });
  });
}

/**
 * Decode the JWT `exp` (seconds since epoch) from an access token, or
 * null if it isn't a parseable JWT. We read the payload only — no
 * signature verification — because the server is the source of truth
 * on validity; this just lets the client decide whether to proactively
 * redeem the refresh token before /me would 401.
 */
function _tokenExp(token) {
  if (!token || typeof token !== 'string') return null;
  const segs = token.split('.');
  if (segs.length < 2) return null;
  try {
    const b64 = segs[1].replace(/-/g, '+').replace(/_/g, '/');
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(b64 + pad));
    return typeof payload?.exp === 'number' ? payload.exp : null;
  } catch {
    return null;
  }
}

let _bootstrapPromise = null;
function ensureBootstrapped() {
  if (_state.bootstrapping) return _bootstrapPromise;
  if (_state.ready) return Promise.resolve();
  if (!_state.token) {
    setState({ ready: true });
    return Promise.resolve();
  }
  _state = { ..._state, bootstrapping: true };
  _bootstrapPromise = (async () => {
    try {
      // Proactive refresh: if the access token has already expired,
      // redeem the refresh token BEFORE calling /me. Otherwise every
      // reload past the 30-min access TTL emits a visible 401 (the
      // browser logs the first /me attempt regardless of how the JS
      // handles the response) and costs an extra round-trip
      // (401 -> refresh -> retry). We only refresh when truly expired
      // so a still-valid token doesn't needlessly rotate the refresh-
      // token chain. The reactive 401 path in apiClient remains as a
      // fallback for clock-skew edge cases.
      const exp = _tokenExp(_state.token);
      if (exp !== null && exp <= Math.floor(Date.now() / 1000)) {
        try {
          const data = await authService.refresh();
          if (data?.access_token) setState({ token: data.access_token });
        } catch {
          // Refresh token also expired/unavailable — fall through to
          // /me, which will 401 and route through the normal logout.
        }
      }
      const me = await authService.me();
      setState({ user: me, ready: true, bootstrapping: false });
    } catch (e) {
      clearTokens();
      setState({ token: null, user: null, ready: true, bootstrapping: false });
    } finally {
      _bootstrapPromise = null;
    }
  })();
  return _bootstrapPromise;
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function getSnapshot() {
  return _state;
}

/**
 * Hook returning { token, user, ready, login, register, logout, refresh }.
 *
 * - `ready` flips true once the bootstrap finishes (or immediately if
 *   there's no token to validate).
 * - `login` stores tokens, sets user, marks ready, returns the response.
 * - `logout` clears tokens, user, and re-marks ready.
 * - `refresh` re-validates the current token by hitting /me.
 */
export function useAuth() {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    // Kick off bootstrap on first mount of any consumer.
    ensureBootstrapped();
  }, []);

  async function login(email, password) {
    const data = await authService.login(email, password);
    setTokens(data.access_token, data.refresh_token);
    setState({ token: data.access_token, user: data.user ?? null, ready: true });
    return data;
  }

  async function register(email, password) {
    const data = await authService.register(email, password);
    setTokens(data.access_token, data.refresh_token);
    setState({ token: data.access_token, user: data.user ?? null, ready: true });
    return data;
  }

  function logout() {
    authService.logout();
    setState({ token: null, user: null, ready: true });
  }

  async function refresh() {
    if (!_state.token) return;
    try {
      const me = await authService.me();
      setState({ user: me });
      return me;
    } catch (e) {
      // Token rejected; force re-login.
      clearTokens();
      setState({ token: null, user: null });
      throw e;
    }
  }

  return {
    token: state.token,
    user: state.user,
    ready: state.ready,
    login,
    register,
    logout,
    refresh,
  };
}

export default useAuth;

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

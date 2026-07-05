import { useEffect, useSyncExternalStore } from 'react';
import { getToken, setTokens, clearTokens } from '../services/apiClient.js';
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

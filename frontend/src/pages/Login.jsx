import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import { isSafeNext } from '../App.jsx';

/**
 * Normalize any error coming back from authService / apiClient into
 * a single user-friendly string. The backend may return:
 *   - 400 / 422 with `{ "detail": "Email already registered" }` or
 *     `{ "detail": [ { "msg": "...", "loc": [...] }, ... ] }` (Pydantic)
 *   - 401 with `{ "detail": "Invalid credentials" }`
 *   - network errors with `Cannot reach the server.`
 */
function formatError(e) {
  if (!e) return 'Something went wrong.';
  if (e.body && Array.isArray(e.body.detail)) {
    return e.body.detail
      .map((d) => d.msg || JSON.stringify(d))
      .join('; ');
  }
  if (e.body && e.body.detail) return String(e.body.detail);
  if (e.body && e.body.message) return String(e.body.message);
  if (e.message) return e.message;
  return 'Something went wrong.';
}

export default function Login() {
  const { login, register, token, ready } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const nextRaw = new URLSearchParams(loc.search).get('next') || '/';
  const next = isSafeNext(nextRaw) ? nextRaw : '/';

  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  // If the user is already authenticated (e.g. just registered and
  // tokens are set), send them on.
  useEffect(() => {
    if (ready && token) {
      nav(next, { replace: true });
    }
  }, [ready, token, next, nav]);

  // Clear the error when switching modes so old messages don't linger.
  function switchMode() {
    setErr(null);
    setMode((m) => (m === 'login' ? 'register' : 'login'));
  }

  async function onSubmit(e) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      if (mode === 'login') {
        await login(email, password);
      } else {
        await register(email, password);
      }
      // The auth singleton has flipped `token` to truthy; the
      // useEffect above will navigate.
    } catch (e2) {
      setErr(formatError(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={onSubmit} aria-busy={busy}>
        <h1>Project Athena</h1>
        <p style={{ color: 'var(--text-dim)', marginTop: 0 }}>
          {mode === 'login' ? 'Sign in to your account' : 'Create an account'}
        </p>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          autoFocus
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
        />
        {err && (
          <div className="err" role="alert">
            {err}
          </div>
        )}
        <div className="actions">
          <button
            type="button"
            className="secondary"
            onClick={switchMode}
            disabled={busy}
          >
            {mode === 'login' ? 'Need an account?' : 'Have an account?'}
          </button>
          <button type="submit" disabled={busy}>
            {busy ? '…' : mode === 'login' ? 'Sign in' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  );
}

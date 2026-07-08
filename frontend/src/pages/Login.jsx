import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { LogIn, UserPlus, Eye, EyeOff, Sparkles } from 'lucide-react';
import { useAuth } from '../hooks/useAuth.js';
import { isSafeNext } from '../App.jsx';
import Button from '../components/ui/Button.jsx';
import Input from '../components/ui/Input.jsx';
import { FormField } from '../components/ui/Input.jsx';
import { fadeUp } from '../components/ui/Motion.jsx';

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
  const [showPw, setShowPw] = useState(false);

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

  const isRegister = mode === 'register';

  return (
    <div className="min-h-screen w-screen flex items-center justify-center bg-background px-4 py-10">
      <motion.div
        variants={fadeUp}
        initial="hidden"
        animate="show"
        className="w-full max-w-[420px]"
      >
        <div className="flex flex-col items-center text-center mb-8">
          <div className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--accent)] text-[var(--accent-fg)] mb-4">
            <Sparkles size={20} strokeWidth={1.75} />
          </div>
          <h1 className="text-h1 font-medium tracking-tight text-ink">
            Project Athena
          </h1>
          <p className="mt-1.5 text-sm text-ink-dim">
            {isRegister
              ? 'Create an account to get started.'
              : 'Welcome back. Sign in to continue.'}
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          aria-busy={busy}
          className="rounded-2xl border border-hairline bg-surface shadow-floating p-6 space-y-4"
        >
          <FormField label="Email">
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              autoFocus
              placeholder="you@example.com"
            />
          </FormField>

          <FormField
            label="Password"
            hint={isRegister ? 'min. 8 characters' : undefined}
          >
            <div className="relative">
              <Input
                type={showPw ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={8}
                required
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                placeholder="••••••••"
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                aria-label={showPw ? 'Hide password' : 'Show password'}
                className="absolute right-2 top-1/2 -translate-y-1/2 inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-faint hover:text-ink hover:bg-surface-2 transition-colors"
              >
                {showPw ? <EyeOff size={14} strokeWidth={1.75} /> : <Eye size={14} strokeWidth={1.75} />}
              </button>
            </div>
          </FormField>

          {err && (
            <div
              role="alert"
              className="rounded-lg border border-[var(--danger)]/40 bg-[var(--danger-bg)] px-3 py-2.5 text-sm text-[var(--danger)]"
            >
              {err}
            </div>
          )}

          <div className="flex items-center justify-between gap-3 pt-2">
            <button
              type="button"
              onClick={switchMode}
              disabled={busy}
              className="text-sm text-ink-dim hover:text-ink transition-colors"
            >
              {isRegister ? 'Have an account?' : 'Need an account?'}
            </button>
            <Button type="submit" disabled={busy}>
              {busy ? (
                <span className="inline-flex items-center gap-2">
                  <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
                  {isRegister ? 'Creating…' : 'Signing in…'}
                </span>
              ) : isRegister ? (
                <>
                  <UserPlus size={14} strokeWidth={1.75} />
                  Create account
                </>
              ) : (
                <>
                  <LogIn size={14} strokeWidth={1.75} />
                  Sign in
                </>
              )}
            </Button>
          </div>
        </form>

        <p className="mt-6 text-center text-xs text-ink-faint">
          A document-grounded AI workspace.
        </p>
      </motion.div>
    </div>
  );
}

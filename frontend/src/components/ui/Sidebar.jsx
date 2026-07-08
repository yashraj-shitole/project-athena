import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LogOut, FileText, MessageSquare, Brain } from 'lucide-react';
import { cn } from '../../lib/cn.js';
import Avatar from './Avatar.jsx';
import Button from './Button.jsx';
import { useAuth } from '../../hooks/useAuth.js';

/**
 * Sidebar — the warm-paper navigation rail. The brief calls for
 * clean, minimal navigation; we keep the conversation list inside
 * the chat shell rather than here so the sidebar is just app nav +
 * user identity.
 */
const NAV_ITEMS = [
  { to: '/',           label: 'Documents', icon: FileText },
  { to: '/chat',       label: 'Chat',      icon: MessageSquare },
  { to: '/connectors', label: 'Models',    icon: Brain },
];

export function Sidebar({ children, className }) {
  const { user, logout } = useAuth();
  const loc = useLocation();
  return (
    <aside
      className={cn(
        'flex flex-col h-full w-[260px] shrink-0',
        'border-r border-hairline bg-surface',
        className,
      )}
    >
      <div className="px-5 py-5">
        <Link to="/" className="flex items-center gap-2 group">
          <Logo />
          <span className="text-base font-medium tracking-tight text-ink">Athena</span>
        </Link>
      </div>

      {children && (
        <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-3">
          {children}
        </div>
      )}

      <nav className="px-3 pb-3">
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => {
            const isActive = to === '/'
              ? loc.pathname === '/' || loc.pathname.startsWith('/documents')
              : loc.pathname.startsWith(to);
            return (
              <li key={to}>
                <Link
                  to={to}
                  className={cn(
                    'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm',
                    'transition-colors duration-[var(--motion-fast)]',
                    isActive
                      ? 'bg-surface-2 text-ink font-medium'
                      : 'text-ink-dim hover:bg-surface-2/60 hover:text-ink',
                  )}
                >
                  <Icon size={16} strokeWidth={1.75} className="shrink-0" />
                  <span>{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="mt-auto px-3 pb-4 pt-3 border-t border-hairline">
        <div className="flex items-center gap-2.5 px-2 py-1.5">
          <Avatar name={user?.email || 'User'} size={28} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-ink">{user?.email || '—'}</p>
          </div>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            logout();
          }}
          className="mt-1.5"
        >
          <Button
            type="submit"
            variant="ghost"
            size="sm"
            className="w-full justify-start text-ink-dim"
          >
            <LogOut size={14} strokeWidth={1.75} />
            Sign out
          </Button>
        </form>
      </div>
    </aside>
  );
}

function Logo() {
  return (
    <span
      className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-[var(--accent)] text-[var(--accent-fg)]"
      aria-hidden
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        <path d="M7 1L11.5 4V10L7 13L2.5 10V4L7 1Z" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" />
        <circle cx="7" cy="7" r="1.6" fill="currentColor" />
      </svg>
    </span>
  );
}

export default Sidebar;

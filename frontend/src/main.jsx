import React, { useEffect } from 'react';
import ReactDOM from 'react-dom/client';
import {
  BrowserRouter,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import App, { isSafeNext } from './App.jsx';
import { AUTH_EVENT } from './services/apiClient.js';
import { ToastProvider } from './components/ui/Toaster.jsx';
import { TooltipProvider } from './components/ui/Tooltip.jsx';
import './index.css';

/**
 * Listens for `athena:auth-failed` (dispatched by apiClient on 401)
 * and navigates to /login via React Router — preserving the user's
 * current path so they can be sent back after re-authenticating.
 *
 * Why not just window.location.href? That tears down the React tree
 * and loses in-memory state (chat store, current conversation, etc.).
 * Using the router keeps the app mounted so the login page can read
 * `?next=` and the back button works naturally.
 */
function AuthBoundary({ children }) {
  const nav = useNavigate();
  const loc = useLocation();
  useEffect(() => {
    const onAuth = () => {
      if (loc.pathname === '/login') return;
      const next = encodeURIComponent(loc.pathname + loc.search);
      nav(`/login?next=${next}`, { replace: true });
    };
    window.addEventListener(AUTH_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_EVENT, onAuth);
  }, [nav, loc.pathname, loc.search]);
  // Touch isSafeNext so the App.jsx export is used; we don't
  // actually need it here, but having both files co-locate the
  // helper makes it easy to share.
  void isSafeNext;
  return children;
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <TooltipProvider>
        <ToastProvider>
          <AuthBoundary>
            <App />
          </AuthBoundary>
        </ToastProvider>
      </TooltipProvider>
    </BrowserRouter>
  </React.StrictMode>,
);

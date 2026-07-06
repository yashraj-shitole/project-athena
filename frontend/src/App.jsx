import React from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import ChatInterface from './pages/ChatInterface.jsx';
import DocumentDetail from './pages/DocumentDetail.jsx';
import DocumentManager from './pages/DocumentManager.jsx';
import Login from './pages/Login.jsx';
import { useAuth } from './hooks/useAuth.js';

/**
 * Same-origin path validator. We accept only paths that start with `/`
 * and are not `//` (which the browser treats as protocol-relative).
 * Used for the `?next=` redirect target to prevent open-redirect.
 */
function isSafeNext(next) {
  if (typeof next !== 'string' || next.length === 0) return false;
  if (!next.startsWith('/')) return false;
  if (next.startsWith('//')) return false;
  if (next.startsWith('/\\')) return false;
  return true;
}

function Protected({ children }) {
  const { token, ready } = useAuth();
  const location = useLocation();
  if (!ready) {
    return (
      <div
        role="status"
        aria-live="polite"
        style={{
          padding: 24,
          color: 'var(--text-dim)',
          fontFamily: 'system-ui, sans-serif',
        }}
      >
        Loading…
      </div>
    );
  }
  if (!token) {
    // Preserve where the user was trying to go so login can send them back.
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return children;
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <Protected>
            <DocumentManager />
          </Protected>
        }
      />
      <Route
        path="/chat"
        element={
          <Protected>
            <ChatInterface />
          </Protected>
        }
      />
      <Route
        path="/chat/:conversationId"
        element={
          <Protected>
            <ChatInterface />
          </Protected>
        }
      />
      <Route
        path="/documents/:id"
        element={
          <Protected>
            <DocumentDetail />
          </Protected>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export { isSafeNext };
export default App;

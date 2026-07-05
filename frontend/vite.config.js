import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite proxies all backend calls during development. The target can be
// overridden with VITE_API_TARGET (e.g. for a remote backend).
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/health': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/model': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/metrics': {
        target: API_TARGET,
        changeOrigin: true,
      },
      // SSE-friendly: ensure Vite doesn't buffer the response.
      // (Default is fine for `fetch` with a reader, but we configure
      // the websocket-like options explicitly for clarity.)
      '/api/chat/stream': {
        target: API_TARGET,
        changeOrigin: true,
        // The response body is held open by the backend; do not
        // proxy-timeout. We rely on the client to abort.
        proxyTimeout: undefined,
      },
    },
  },
  build: {
    // Stream-friendly chunking so the chat UI bundle can load in
    // parallel with the rest.
    target: 'es2020',
  },
});

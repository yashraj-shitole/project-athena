/**
 * Connectors store (Zustand).
 *
 * Holds the user's registered AI provider connectors plus the
 * currently-active `(connectorId, model)` pair. The chat UI
 * reads `activeModel` to know which model to send the
 * request to; the `Connectors` page reads/writes `byId` and
 * `list` to manage connectors.
 *
 * `activeModel` persists in `localStorage` so a page reload
 * (or a new tab) doesn't reset the picker to the built-in
 * Ollama fallback.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import connectorService from '../services/connectorService.js';

const useConnectorsStore = create(
  persist(
    (set, get) => ({
      // --- State ---------------------------------------------------------
      list: [],                  // array of ModelConnectorPublic
      templates: [],             // canned {provider, default_base_url, capabilities}
      registry: [],              // flat list of {provider, class} for the picker
      byId: {},                  // id -> connector (mirrors list)
      loading: false,
      error: null,
      lastLoadedAt: 0,
      activeModel: {
        // null = built-in Ollama fallback
        connectorId: null,
        model: null,
      },

      // --- Actions -------------------------------------------------------

      /** Fetch the full list (and templates + registry in one roundtrip). */
      async load(force = false) {
        const now = Date.now();
        if (!force && get().list.length && now - get().lastLoadedAt < 30_000) {
          return; // 30s cache to avoid hammering /api/connectors on every nav
        }
        set({ loading: true, error: null });
        try {
          const res = await connectorService.list();
          const list = res?.connectors || [];
          const templates = res?.templates || [];
          const registry = res?.registry || [];
          const byId = Object.fromEntries(list.map((c) => [c.id, c]));
          set({
            list,
            templates,
            registry,
            byId,
            loading: false,
            lastLoadedAt: Date.now(),
          });
        } catch (err) {
          set({ loading: false, error: err?.message || String(err) });
        }
      },

      async create(payload) {
        const created = await connectorService.create(payload);
        // Refetch the full list — `is_default` invariant may have changed.
        await get().load(true);
        return created;
      },

      async update(id, payload) {
        const updated = await connectorService.update(id, payload);
        await get().load(true);
        return updated;
      },

      async remove(id) {
        await connectorService.remove(id);
        // If the deleted one was the active model, reset to fallback.
        if (get().activeModel.connectorId === id) {
          set({ activeModel: { connectorId: null, model: null } });
        }
        await get().load(true);
      },

      async clone(id) {
        const dup = await connectorService.clone(id);
        await get().load(true);
        return dup;
      },

      async setDefault(id) {
        await connectorService.setDefault(id);
        await get().load(true);
      },

      /**
       * Pick `(connectorId, model)` as the chat target.
       * Pass `null` to use the built-in Ollama fallback.
       */
      setActiveModel({ connectorId = null, model = null } = {}) {
        set({ activeModel: { connectorId, model } });
      },

      /**
       * Reset to the user's default connector, or the built-in
       * Ollama fallback if none is set.
       */
      async syncActiveToDefault() {
        const list = get().list;
        // Prefer a row flagged `is_default`. Otherwise the first enabled
        // admin-shared row. Otherwise null (Ollama fallback).
        const def =
          list.find((c) => c.is_default && c.is_enabled) ||
          list.find((c) => c.is_admin && c.is_enabled) ||
          null;
        if (def) {
          set({
            activeModel: { connectorId: def.id, model: def.default_model },
          });
        } else {
          set({ activeModel: { connectorId: null, model: null } });
        }
      },
    }),
    {
      name: 'athena.connectors',
      // Only persist the active model — the connector list is large and
      // can go stale; it's reloaded on mount.
      partialize: (s) => ({ activeModel: s.activeModel }),
    },
  ),
);

export default useConnectorsStore;

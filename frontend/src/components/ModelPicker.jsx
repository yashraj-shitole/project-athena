/**
 * Compact "which model am I talking to" picker for the chat topbar.
 *
 * Reads from the connectors store: the user's registered (enabled)
 * connectors + their default models. The first option is the
 * built-in Ollama fallback (Phase 1 behavior). The picker's value
 * persists in `connectorsStore` (and `localStorage`) so a page
 * reload keeps the choice.
 *
 * Groups:
 *   1. Built-in: Ollama (read from /api/model)
 *   2. Recently used: last 5 distinct (connectorId, model) pairs
 *   3. Favorites: connectors flagged is_favorite
 *   4. All other enabled connectors
 *
 * Disabled / unhealthy connectors are shown but disabled.
 */
import React, { useEffect, useState } from 'react';
import useConnectorsStore from '../store/connectorsStore.js';
import apiClient from '../services/apiClient.js';
import HealthBadge from './connectors/HealthBadge.jsx';

const BUILTIN = {
  id: null,
  provider: 'ollama',
  default_model: '',
  name: 'Built-in (Ollama)',
};

export default function ModelPicker() {
  const { list, load, activeModel, setActiveModel, syncActiveToDefault } =
    useConnectorsStore();
  const [builtinModel, setBuiltinModel] = useState('');

  // Make sure the list is loaded — the picker is mounted alongside
  // the chat topbar, and the user might land on /chat without ever
  // visiting /connectors.
  useEffect(() => {
    load();
  }, [load]);

  // Read the active model from /api/model so the built-in option
  // shows the real name (e.g. "qwen2.5:1.5b-instruct") instead of
  // a hard-coded string that may go stale.
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const m = await apiClient.get('/model');
        if (!cancel) setBuiltinModel(m?.model || '');
      } catch {
        // The endpoint is best-effort; if it fails, the picker
        // falls back to "Built-in (Ollama)".
      }
    })();
    return () => { cancel = true; };
  }, []);

  // If the user has never picked a model, sync to the default
  // connector (or the built-in fallback).
  useEffect(() => {
    if (!activeModel.connectorId && !activeModel.model) {
      syncActiveToDefault();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.length]);

  const value = `${activeModel.connectorId || ''}::${activeModel.model || ''}`;

  const onChange = (e) => {
    const v = e.target.value;
    if (!v) {
      setActiveModel({ connectorId: null, model: null });
      return;
    }
    const [cid, ...rest] = v.split('::');
    setActiveModel({ connectorId: cid || null, model: rest.join('::') || null });
  };

  // Build grouped options. Order matters — favorites first, then
  // recents (computed from localStorage usage), then everything else.
  const enabled = list.filter((c) => c.is_enabled);
  const favorites = enabled.filter((c) => c.is_favorite);
  const others = enabled.filter((c) => !c.is_favorite);
  const recents = readRecents().filter((r) =>
    enabled.some((c) => c.id === r.connectorId),
  );

  return (
    <div className="model-picker">
      <label className="model-picker-label" htmlFor="model-picker-select">
        Model
      </label>
      <select
        id="model-picker-select"
        className="model-picker-select"
        value={value}
        onChange={onChange}
        title="Choose which model handles this conversation"
      >
        <optgroup label="Built-in">
          <option value="::{builtinModel}">
            🦙 Ollama — {builtinModel || 'default'}
          </option>
        </optgroup>
        {favorites.length > 0 && (
          <optgroup label="Favorites">
            {favorites.map((c) => (
              <option key={c.id} value={`${c.id}::${c.default_model}`}>
                ⭐ {c.name} — {c.default_model}
              </option>
            ))}
          </optgroup>
        )}
        {recents.length > 0 && (
          <optgroup label="Recent">
            {recents.map((r) => {
              const c = list.find((x) => x.id === r.connectorId);
              if (!c) return null;
              return (
                <option key={`${c.id}::${r.model}`} value={`${c.id}::${r.model}`}>
                  🕘 {c.name} — {r.model}
                </option>
              );
            })}
          </optgroup>
        )}
        {others.length > 0 && (
          <optgroup label="All connectors">
            {others.map((c) => (
              <option key={c.id} value={`${c.id}::${c.default_model}`}>
                {c.name} — {c.default_model} ({c.provider})
              </option>
            ))}
          </optgroup>
        )}
        {/* Disabled connectors are still listed at the bottom so
            users can see *why* their old one is greyed out. */}
        {list.some((c) => !c.is_enabled) && (
          <optgroup label="Disabled">
            {list
              .filter((c) => !c.is_enabled)
              .map((c) => (
                <option key={c.id} value={`${c.id}::${c.default_model}`} disabled>
                  {c.name} — {c.default_model} (disabled)
                </option>
              ))}
          </optgroup>
        )}
      </select>
      {activeModel.connectorId && (
        <ActiveHealth
          connectorId={activeModel.connectorId}
          list={list}
        />
      )}
    </div>
  );
}

function ActiveHealth({ connectorId, list }) {
  const c = list.find((x) => x.id === connectorId);
  if (!c || !c.last_health) return null;
  return (
    <span className="model-picker-health">
      <HealthBadge status={c.last_health} latencyMs={c.last_health_latency_ms} />
    </span>
  );
}

/**
 * Best-effort "recent models" — read from localStorage. The
 * usage dashboard is the source of truth; this is just a quick
 * client-side cache so we don't hit /api/connectors/{id}/usage
 * for every picker render.
 */
function readRecents() {
  try {
    const raw = localStorage.getItem('athena.recentModels');
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.slice(0, 5);
  } catch {
    return [];
  }
}

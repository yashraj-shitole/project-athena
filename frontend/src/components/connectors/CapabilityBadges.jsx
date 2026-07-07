/**
 * Inline row of capability pills for a connector.
 *
 * Reads `capabilities` from the public connector schema
 * (e.g. `{chat: true, stream: true, tools: false, vision: true,
 * embeddings: false, json_mode: true, structured: false}`) and
 * renders a small pill for each that is true.
 */
import React from 'react';

const LABELS = {
  chat: 'Chat',
  stream: 'Stream',
  tools: 'Tools',
  vision: 'Vision',
  embeddings: 'Embeddings',
  json_mode: 'JSON',
  structured: 'Structured',
};

const ICONS = {
  chat: '💬',
  stream: '⚡',
  tools: '🛠',
  vision: '👁',
  embeddings: '🧮',
  json_mode: '{ }',
  structured: '📋',
};

export default function CapabilityBadges({ capabilities }) {
  if (!capabilities || typeof capabilities !== 'object') return null;
  const enabled = Object.entries(capabilities).filter(([, v]) => !!v);
  if (!enabled.length) {
    return <span className="capability-empty">no capabilities detected</span>;
  }
  return (
    <div className="capability-row">
      {enabled.map(([k]) => (
        <span key={k} className="capability-badge" title={LABELS[k] || k}>
          <span className="capability-icon">{ICONS[k] || '•'}</span>
          {LABELS[k] || k}
        </span>
      ))}
    </div>
  );
}

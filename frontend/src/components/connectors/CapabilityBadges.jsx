/**
 * Inline row of capability pills for a connector.
 *
 * Reads `capabilities` from the public connector schema
 * (e.g. `{chat: true, stream: true, tools: false, vision: true,
 * embeddings: false, json_mode: true, structured: false}`) and
 * renders a small pill for each that is true.
 */
import React from 'react';
import { MessageSquare, Zap, Wrench, Eye, Binary, Braces, ClipboardList } from 'lucide-react';
import Badge from '../ui/Badge.jsx';

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
  chat: MessageSquare,
  stream: Zap,
  tools: Wrench,
  vision: Eye,
  embeddings: Binary,
  json_mode: Braces,
  structured: ClipboardList,
};

export default function CapabilityBadges({ capabilities }) {
  if (!capabilities || typeof capabilities !== 'object') return null;
  const enabled = Object.entries(capabilities).filter(([, v]) => !!v);
  if (!enabled.length) {
    return <span className="text-xs text-ink-faint italic">no capabilities detected</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {enabled.map(([k]) => {
        const Icon = ICONS[k];
        return (
          <Badge key={k} tone="neutral" size="md" title={LABELS[k] || k}>
            {Icon && <Icon size={11} strokeWidth={1.75} />}
            {LABELS[k] || k}
          </Badge>
        );
      })}
    </div>
  );
}

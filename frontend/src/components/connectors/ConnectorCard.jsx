/**
 * A single connector summary card used in the Connectors page
 * list view. Shows name, provider, default model, capabilities,
 * last health, and action buttons (Test, Edit, Clone, Set default,
 * Delete).
 */
import React from 'react';
import { MoreHorizontal, Edit3, Copy, Star, Trash2, Zap, RefreshCw, BarChart3, ClipboardList, Plug } from 'lucide-react';
import HealthBadge from './HealthBadge.jsx';
import CapabilityBadges from './CapabilityBadges.jsx';
import Card from '../ui/Card.jsx';
import Button from '../ui/Button.jsx';
import Badge from '../ui/Badge.jsx';
import {
  DropdownMenu, DropdownItem, DropdownSeparator, DropdownLabel,
} from '../ui/DropdownMenu.jsx';
import { cn } from '../../lib/cn.js';

const PROVIDER_ICON = {
  openai_compat: '🟢',
  anthropic: '🟣',
  gemini: '🔵',
  azure_openai: '🟦',
  ollama: '🦙',
  custom: '⚙️',
};

export default function ConnectorCard({
  connector,
  onEdit,
  onDelete,
  onClone,
  onSetDefault,
  onTest,
  onRefreshModels,
  onViewUsage,
  onViewAudit,
  testing,
}) {
  const {
    id,
    name,
    provider,
    base_url,
    default_model,
    models,
    is_enabled,
    is_default,
    is_admin,
    last_health,
    last_health_latency_ms,
    capabilities,
  } = connector;

  return (
    <Card className={cn('p-4', !is_enabled && 'opacity-60')}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-base">
            <span aria-hidden>{PROVIDER_ICON[provider] || '🧠'}</span>
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-sm font-medium tracking-tight text-ink truncate">
                {name}
              </h3>
              {is_default && <Badge tone="solid" size="sm">default</Badge>}
              {is_admin && <Badge tone="neutral" size="sm">shared</Badge>}
              {!is_enabled && <Badge tone="warn" size="sm">disabled</Badge>}
            </div>
            <p className="text-xs text-ink-dim mt-0.5 truncate flex items-center gap-1.5 flex-wrap">
              <code className="text-[11px] bg-surface-2 px-1.5 py-0.5 rounded">{provider}</code>
              <span>·</span>
              <span>model:</span>
              <code className="text-[11px]">{default_model}</code>
              {base_url && (
                <>
                  <span>·</span>
                  <a
                    href={base_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-ink-dim hover:text-ink underline underline-offset-2 max-w-[240px] truncate"
                    title={base_url}
                  >
                    {base_url}
                  </a>
                </>
              )}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <HealthBadge status={last_health} latencyMs={last_health_latency_ms} />
          <DropdownMenu
            trigger={
              <Button variant="ghost" size="icon-sm" aria-label="More actions">
                <MoreHorizontal size={15} strokeWidth={1.75} />
              </Button>
            }
          >
            <DropdownItem onSelect={() => onTest(connector)} disabled={testing}>
              <Plug size={14} strokeWidth={1.75} />
              Test
            </DropdownItem>
            <DropdownItem onSelect={() => onRefreshModels(connector)}>
              <RefreshCw size={14} strokeWidth={1.75} />
              Refresh models
            </DropdownItem>
            <DropdownSeparator />
            <DropdownItem onSelect={() => onEdit(connector)}>
              <Edit3 size={14} strokeWidth={1.75} />
              Edit
            </DropdownItem>
            <DropdownItem onSelect={() => onClone(connector)}>
              <Copy size={14} strokeWidth={1.75} />
              Clone
            </DropdownItem>
            {!is_default && is_enabled && (
              <DropdownItem onSelect={() => onSetDefault(connector)}>
                <Star size={14} strokeWidth={1.75} />
                Set default
              </DropdownItem>
            )}
            <DropdownSeparator />
            <DropdownLabel>Insights</DropdownLabel>
            <DropdownItem onSelect={() => onViewUsage(connector)}>
              <BarChart3 size={14} strokeWidth={1.75} />
              Usage
            </DropdownItem>
            <DropdownItem onSelect={() => onViewAudit(connector)}>
              <ClipboardList size={14} strokeWidth={1.75} />
              Audit log
            </DropdownItem>
            <DropdownSeparator />
            <DropdownItem danger onSelect={() => onDelete(connector)}>
              <Trash2 size={14} strokeWidth={1.75} />
              Delete
            </DropdownItem>
          </DropdownMenu>
        </div>
      </div>

      <div className="mt-3">
        <CapabilityBadges capabilities={capabilities} />
      </div>

      {Array.isArray(models) && models.length > 0 && (
        <details className="mt-3 group/det">
          <summary className="cursor-pointer text-xs text-ink-dim hover:text-ink transition-colors list-none flex items-center gap-1.5">
            <span className="inline-block group-open/det:rotate-90 transition-transform">▸</span>
            {models.length} model{models.length === 1 ? '' : 's'} exposed
          </summary>
          <ul className="mt-2 flex flex-col gap-1 pl-3.5">
            {models.map((m) => (
              <li key={m} className="text-xs text-ink-dim font-mono truncate">{m}</li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

/**
 * A single connector summary card used in the Connectors page
 * list view. Shows name, provider, default model, capabilities,
 * last health, and action buttons (Test, Edit, Clone, Set default,
 * Delete).
 */
import React from 'react';
import HealthBadge from './HealthBadge.jsx';
import CapabilityBadges from './CapabilityBadges.jsx';

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
    <div className={`connector-card ${!is_enabled ? 'is-disabled' : ''}`}>
      <div className="connector-card-header">
        <div className="connector-card-title">
          <span className="connector-provider-icon" aria-hidden>
            {PROVIDER_ICON[provider] || '🧠'}
          </span>
          <h3>{name}</h3>
          {is_default && <span className="connector-default-tag">default</span>}
          {is_admin && <span className="connector-shared-tag">shared</span>}
        </div>
        <HealthBadge status={last_health} latencyMs={last_health_latency_ms} />
      </div>

      <div className="connector-card-meta">
        <code className="connector-provider">{provider}</code>
        <span className="connector-meta-sep">·</span>
        <span className="connector-model">model: <code>{default_model}</code></span>
        <span className="connector-meta-sep">·</span>
        <a
          href={base_url}
          target="_blank"
          rel="noreferrer"
          className="connector-base-url"
          title={base_url}
        >
          {base_url}
        </a>
      </div>

      <CapabilityBadges capabilities={capabilities} />

      {Array.isArray(models) && models.length > 0 && (
        <details className="connector-models-disclosure">
          <summary>{models.length} model{models.length === 1 ? '' : 's'} exposed</summary>
          <ul className="connector-models-list">
            {models.map((m) => (
              <li key={m}><code>{m}</code></li>
            ))}
          </ul>
        </details>
      )}

      <div className="connector-card-actions">
        <button type="button" onClick={() => onTest(connector)} disabled={testing}>
          {testing ? 'Testing…' : 'Test'}
        </button>
        <button type="button" onClick={() => onRefreshModels(connector)}>
          Refresh models
        </button>
        <button type="button" onClick={() => onEdit(connector)}>
          Edit
        </button>
        <button type="button" onClick={() => onClone(connector)}>
          Clone
        </button>
        {!is_default && is_enabled && (
          <button type="button" onClick={() => onSetDefault(connector)}>
            Set default
          </button>
        )}
        <button type="button" onClick={() => onViewUsage(connector)}>
          Usage
        </button>
        <button type="button" onClick={() => onViewAudit(connector)}>
          Audit
        </button>
        <button
          type="button"
          className="connector-danger"
          onClick={() => onDelete(connector)}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

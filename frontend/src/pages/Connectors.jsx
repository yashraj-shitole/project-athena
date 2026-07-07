/**
 * Connectors management page.
 *
 * Three sections: list of registered connectors (cards), a
 * "Add connector" button that opens the create dialog, and
 * per-connector tabs (usage, audit, models, health) that
 * slide in when the user picks one.
 *
 * The page is the canonical "explain the system" view: it
 * shows the live health status, the discovered model list,
 * and the audit trail in one place.
 */
import React, { useEffect, useState } from 'react';
import useConnectorsStore from '../store/connectorsStore.js';
import ConnectorCard from '../components/connectors/ConnectorCard.jsx';
import ConnectorDialog from '../components/connectors/ConnectorDialog.jsx';
import ModelDiscoveryPanel from '../components/connectors/ModelDiscoveryPanel.jsx';
import UsageDashboard from '../components/connectors/UsageDashboard.jsx';
import AuditLogTable from '../components/connectors/AuditLogTable.jsx';
import TestPanel from '../components/connectors/TestPanel.jsx';

export default function Connectors() {
  const {
    list,
    templates,
    loading,
    error,
    load,
    create,
    update,
    remove,
    clone,
    setDefault,
  } = useConnectorsStore();

  const [editing, setEditing] = useState(null);     // null | 'new' | ModelConnectorPublic
  const [selected, setSelected] = useState(null);   // ModelConnectorPublic | null
  const [tab, setTab] = useState('overview');       // overview|usage|audit|models|test
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    load(true);
  }, [load]);

  const onCreate = async (payload) => {
    setBusy(true);
    try { await create(payload); }
    finally { setBusy(false); }
  };
  const onUpdate = async (payload) => {
    if (!editing || editing === 'new') return;
    setBusy(true);
    try { await update(editing.id, payload); }
    finally { setBusy(false); }
  };
  const onDelete = async (c) => {
    if (!confirm(`Delete "${c.name}"? This is reversible by an admin (soft delete).`)) return;
    setBusy(true);
    try { await remove(c.id); if (selected?.id === c.id) setSelected(null); }
    finally { setBusy(false); }
  };
  const onClone = async (c) => {
    setBusy(true);
    try { await clone(c.id); }
    finally { setBusy(false); }
  };
  const onSetDefault = async (c) => {
    setBusy(true);
    try { await setDefault(c.id); }
    finally { setBusy(false); }
  };

  const onTest = (c) => {
    setSelected(c);
    setTab('test');
  };

  return (
    <div className="page connectors-page">
      <div className="connectors-header">
        <h1>External Model Connectors</h1>
        <p className="connectors-blurb">
          Register AI providers (OpenAI, Anthropic, Gemini, Azure, Ollama, or a
          custom REST endpoint) and have them act exactly like the built-in
          model. API keys are encrypted at rest; you can rotate them at any
          time. <a href="/docs/connectors" target="_blank" rel="noreferrer">Learn more</a>.
        </p>
        <div className="connectors-toolbar">
          <button
            type="button"
            className="primary"
            onClick={() => setEditing('new')}
            disabled={busy}
          >
            + Add connector
          </button>
          <button type="button" onClick={() => load(true)} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && <div className="test-panel-error">⚠ {error}</div>}

      <div className="connectors-grid">
        <div className="connectors-list">
          {!list.length && !loading && (
            <div className="connectors-empty">
              No connectors yet. Click <strong>Add connector</strong> to register
              your first external model.
            </div>
          )}
          {list.map((c) => (
            <ConnectorCard
              key={c.id}
              connector={c}
              onEdit={(c) => setEditing(c)}
              onDelete={onDelete}
              onClone={onClone}
              onSetDefault={onSetDefault}
              onTest={onTest}
              onRefreshModels={(c) => { setSelected(c); setTab('models'); }}
              onViewUsage={(c) => { setSelected(c); setTab('usage'); }}
              onViewAudit={(c) => { setSelected(c); setTab('audit'); }}
              testing={busy}
            />
          ))}
        </div>

        {selected && (
          <div className="connectors-detail">
            <div className="connectors-detail-header">
              <h2>{selected.name}</h2>
              <button
                type="button"
                className="modal-close"
                onClick={() => setSelected(null)}
                aria-label="Close detail"
              >
                ×
              </button>
            </div>
            <div className="tab-bar">
              {['overview', 'usage', 'models', 'audit', 'test'].map((t) => (
                <button
                  key={t}
                  type="button"
                  className={tab === t ? 'tab tab-active' : 'tab'}
                  onClick={() => setTab(t)}
                >
                  {t}
                </button>
              ))}
            </div>
            <div className="tab-body">
              {tab === 'overview' && <OverviewTab connector={selected} />}
              {tab === 'usage' && <UsageDashboard connector={selected} />}
              {tab === 'models' && <ModelDiscoveryPanel connector={selected} />}
              {tab === 'audit' && <AuditLogTable connector={selected} />}
              {tab === 'test' && <TestPanel connector={selected} />}
            </div>
          </div>
        )}
      </div>

      <ConnectorDialog
        open={editing !== null}
        initial={editing === 'new' ? null : editing}
        templates={templates}
        onClose={() => setEditing(null)}
        onSubmit={editing === 'new' ? onCreate : onUpdate}
      />
    </div>
  );
}

function OverviewTab({ connector }) {
  const {
    provider, base_url, default_model, models,
    is_enabled, is_default, is_admin, is_favorite,
    auth_type, auth_header_name, organization_id, project_id, api_version,
    custom_headers, capabilities, settings,
    tags, group_name, last_health, last_health_at, last_health_latency_ms,
    consecutive_failures, created_at, updated_at,
  } = connector;
  return (
    <div className="overview-tab">
      <Field label="Provider" value={provider} mono />
      <Field label="Base URL" value={base_url} mono />
      <Field label="Default model" value={default_model} mono />
      <Field
        label="Exposed models"
        value={Array.isArray(models) && models.length ? models.join(', ') : '—'}
      />
      <Field label="Auth" value={auth_type} />
      {auth_header_name && <Field label="Auth header" value={auth_header_name} mono />}
      {organization_id && <Field label="Organization" value={organization_id} mono />}
      {project_id && <Field label="Project" value={project_id} mono />}
      {api_version && <Field label="API version" value={api_version} mono />}
      <Field
        label="Status"
        value={
          is_enabled
            ? is_default
              ? 'enabled · default'
              : 'enabled'
            : 'disabled'
        }
      />
      {is_admin && <Field label="Visibility" value="shared (admin)" />}
      {is_favorite && <Field label="Favorite" value="yes" />}
      {group_name && <Field label="Group" value={group_name} />}
      {Array.isArray(tags) && tags.length > 0 && <Field label="Tags" value={tags.join(', ')} />}
      <Field
        label="Last health"
        value={
          last_health
            ? `${last_health}${last_health_latency_ms ? ` · ${last_health_latency_ms}ms` : ''}` +
              (last_health_at ? ` @ ${new Date(last_health_at).toLocaleString()}` : '')
            : '—'
        }
      />
      {consecutive_failures ? (
        <Field label="Consecutive failures" value={String(consecutive_failures)} />
      ) : null}
      <Field
        label="Custom headers"
        value={custom_headers ? JSON.stringify(custom_headers, null, 2) : '—'}
        code
      />
      <Field
        label="Capabilities"
        value={capabilities ? JSON.stringify(capabilities, null, 2) : '—'}
        code
      />
      <Field
        label="Settings"
        value={settings ? JSON.stringify(settings, null, 2) : '—'}
        code
      />
      <Field
        label="Created"
        value={created_at ? new Date(created_at).toLocaleString() : '—'}
      />
      <Field
        label="Updated"
        value={updated_at ? new Date(updated_at).toLocaleString() : '—'}
      />
    </div>
  );
}

function Field({ label, value, mono, code }) {
  if (code) {
    return (
      <div className="overview-field">
        <span className="overview-label">{label}</span>
        <pre className="overview-code">{value}</pre>
      </div>
    );
  }
  return (
    <div className="overview-field">
      <span className="overview-label">{label}</span>
      <span className={mono ? 'overview-value mono' : 'overview-value'}>
        {value}
      </span>
    </div>
  );
}

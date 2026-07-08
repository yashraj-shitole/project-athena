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
import { Plus, RefreshCw, Loader2, AlertCircle } from 'lucide-react';
import useConnectorsStore from '../store/connectorsStore.js';
import ConnectorCard from '../components/connectors/ConnectorCard.jsx';
import ConnectorDialog from '../components/connectors/ConnectorDialog.jsx';
import ModelDiscoveryPanel from '../components/connectors/ModelDiscoveryPanel.jsx';
import UsageDashboard from '../components/connectors/UsageDashboard.jsx';
import AuditLogTable from '../components/connectors/AuditLogTable.jsx';
import TestPanel from '../components/connectors/TestPanel.jsx';
import AppShell from '../components/ui/AppShell.jsx';
import Topbar from '../components/ui/Topbar.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import Button from '../components/ui/Button.jsx';
import Sheet from '../components/ui/Sheet.jsx';
import Tabs, { TabsList, TabsTrigger, TabsContent } from '../components/ui/Tabs.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';
import { useToast } from '../components/ui/Toaster.jsx';
import { motion, AnimatePresence } from 'framer-motion';
import { fadeUp } from '../components/ui/Motion.jsx';
import { Brain as BrainIcon } from 'lucide-react';

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
  const toast = useToast();

  useEffect(() => {
    load(true);
  }, [load]);

  const onCreate = async (payload) => {
    setBusy(true);
    try {
      await create(payload);
      toast.show('Connector created.', { tone: 'success' });
    } catch (e) {
      toast.show(e.message || 'Could not create connector.', { tone: 'error' });
      throw e;
    } finally {
      setBusy(false);
    }
  };
  const onUpdate = async (payload) => {
    if (!editing || editing === 'new') return;
    setBusy(true);
    try {
      await update(editing.id, payload);
      toast.show('Connector updated.', { tone: 'success' });
    } catch (e) {
      toast.show(e.message || 'Could not update connector.', { tone: 'error' });
      throw e;
    } finally {
      setBusy(false);
    }
  };
  const onDelete = async (c) => {
    if (!confirm(`Delete "${c.name}"? This is reversible by an admin (soft delete).`)) return;
    setBusy(true);
    try {
      await remove(c.id);
      if (selected?.id === c.id) setSelected(null);
      toast.show(`Deleted "${c.name}".`, { tone: 'success' });
    } catch (e) {
      toast.show(e.message || 'Could not delete connector.', { tone: 'error' });
    } finally {
      setBusy(false);
    }
  };
  const onClone = async (c) => {
    setBusy(true);
    try {
      await clone(c.id);
      toast.show('Connector cloned. Re-enter the API key to enable it.', { tone: 'success' });
    } catch (e) {
      toast.show(e.message || 'Could not clone connector.', { tone: 'error' });
    } finally {
      setBusy(false);
    }
  };
  const onSetDefault = async (c) => {
    setBusy(true);
    try {
      await setDefault(c.id);
      toast.show(`"${c.name}" is now your default.`, { tone: 'success' });
    } catch (e) {
      toast.show(e.message || 'Could not set default.', { tone: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const onTest = (c) => {
    setSelected(c);
    setTab('test');
  };

  return (
    <AppShell>
      <Topbar>
        <div className="flex items-center gap-2">
          <h1 className="text-base font-medium tracking-tight text-ink">Models</h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => load(true)}
            disabled={loading}
          >
            {loading ? <Loader2 size={14} strokeWidth={1.75} className="animate-spin" /> : <RefreshCw size={14} strokeWidth={1.75} />}
            {loading ? 'Refreshing…' : 'Refresh'}
          </Button>
          <Button onClick={() => setEditing('new')} disabled={busy}>
            <Plus size={14} strokeWidth={1.75} />
            Add connector
          </Button>
        </div>
      </Topbar>

      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-6 py-8 flex flex-col gap-6">
          <PageHeader
            eyebrow="Connectors"
            title="External model providers"
            blurb="Register AI providers (OpenAI, Anthropic, Gemini, Azure, Ollama, or a custom REST endpoint) and have them act exactly like the built-in model. API keys are encrypted at rest; rotate them at any time."
          />

          {error && (
            <div
              role="alert"
              className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3.5 py-2.5 text-sm text-[var(--danger)] flex items-start gap-2"
            >
              <AlertCircle size={16} strokeWidth={1.75} className="mt-0.5 shrink-0" />
              <span className="flex-1">{error}</span>
            </div>
          )}

          {list.length === 0 && !loading ? (
            <EmptyState
              icon={BrainIcon}
              title="No connectors yet"
              description="Register an external model provider to route Athena's chat through it — or rely on the built-in Ollama fallback."
              primaryAction={
                <Button onClick={() => setEditing('new')}>
                  <Plus size={14} strokeWidth={1.75} />
                  Add your first connector
                </Button>
              }
            />
          ) : (
            <motion.div
              variants={fadeUp}
              initial="hidden"
              animate="show"
              className="grid grid-cols-1 lg:grid-cols-2 gap-3"
            >
              <AnimatePresence initial={false}>
                {list.map((c, i) => (
                  <motion.div
                    key={c.id}
                    layout
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0, transition: { delay: i * 0.02 } }}
                    exit={{ opacity: 0 }}
                    onClick={() => setSelected(c)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter') setSelected(c); }}
                    className="cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-hairline-strong focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-xl"
                  >
                    <ConnectorCard
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
                  </motion.div>
                ))}
              </AnimatePresence>
            </motion.div>
          )}
        </div>
      </div>

      <ConnectorDialog
        open={editing !== null}
        initial={editing === 'new' ? null : editing}
        templates={templates}
        onClose={() => setEditing(null)}
        onSubmit={editing === 'new' ? onCreate : onUpdate}
      />

      <Sheet
        open={!!selected}
        onOpenChange={(o) => { if (!o) setSelected(null); }}
        title={selected?.name}
        description={selected ? (
          <span className="text-xs">
            <code className="bg-surface-2 px-1.5 py-0.5 rounded">{selected.provider}</code>
            <span className="mx-1.5 text-ink-faint">·</span>
            <code>{selected.default_model}</code>
          </span>
        ) : null}
        width="lg"
      >
        {selected && (
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="usage">Usage</TabsTrigger>
              <TabsTrigger value="models">Models</TabsTrigger>
              <TabsTrigger value="audit">Audit</TabsTrigger>
              <TabsTrigger value="test">Test</TabsTrigger>
            </TabsList>
            <TabsContent value="overview">
              <OverviewTab connector={selected} />
            </TabsContent>
            <TabsContent value="usage">
              <UsageDashboard connector={selected} />
            </TabsContent>
            <TabsContent value="models">
              <ModelDiscoveryPanel connector={selected} />
            </TabsContent>
            <TabsContent value="audit">
              <AuditLogTable connector={selected} />
            </TabsContent>
            <TabsContent value="test">
              <TestPanel connector={selected} />
            </TabsContent>
          </Tabs>
        )}
      </Sheet>
    </AppShell>
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
    <div className="flex flex-col gap-3 text-sm">
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
      <div className="grid grid-cols-[140px_1fr] gap-3 items-baseline">
        <span className="text-[10px] font-medium uppercase tracking-wider text-ink-faint">{label}</span>
        <pre className="font-mono text-xs bg-surface-2/50 border border-hairline rounded-md px-2.5 py-2 max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words m-0">
          {value}
        </pre>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 items-baseline">
      <span className="text-[10px] font-medium uppercase tracking-wider text-ink-faint">{label}</span>
      <span className={mono ? 'font-mono text-xs break-words' : 'break-words'}>
        {value}
      </span>
    </div>
  );
}

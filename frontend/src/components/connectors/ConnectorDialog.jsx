/**
 * Create / edit dialog for a connector.
 *
 * The form is provider-driven: switching `provider` swaps the
 * default `base_url`, hides irrelevant fields (e.g. Azure's
 * `api_version` shows up only for `azure_openai`), and surfaces
 * a custom-template editor for the `custom` provider.
 *
 * API key handling:
 *   - On create, the plaintext key is sent once and never echoed.
 *   - On edit, the field is prefilled with the masked preview
 *     (e.g. `sk-…1234`). Saving a non-empty value rotates the
 *     key; saving an empty value is "no change".
 *   - A "Reveal preview" toggle shows the preview verbatim.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Eye, EyeOff, AlertCircle, Plug, Loader2, ChevronRight } from 'lucide-react';
import TestPanel from './TestPanel.jsx';
import Dialog from '../ui/Dialog.jsx';
import Input from '../ui/Input.jsx';
import Select from '../ui/Select.jsx';
import Textarea from '../ui/Textarea.jsx';
import Button from '../ui/Button.jsx';

const ALL_PROVIDERS = [
  { value: 'openai_compat', label: 'OpenAI-compatible (OpenAI, Groq, Mistral, …)' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'azure_openai', label: 'Azure OpenAI' },
  { value: 'ollama', label: 'Ollama (native /api/chat)' },
  { value: 'custom', label: 'Custom (template-based)' },
];

const AUTH_TYPES = [
  { value: 'bearer', label: 'Bearer (Authorization: Bearer …)' },
  { value: 'header', label: 'Custom header' },
  { value: 'basic', label: 'HTTP Basic' },
  { value: 'none', label: 'No auth' },
];

function defaultBaseUrlFor(provider) {
  switch (provider) {
    case 'openai_compat': return 'https://api.openai.com/v1';
    case 'anthropic':     return 'https://api.anthropic.com';
    case 'gemini':        return 'https://generativelanguage.googleapis.com';
    case 'azure_openai':  return 'https://YOUR-RESOURCE.openai.azure.com';
    case 'ollama':        return 'http://localhost:11434';
    case 'custom':        return 'https://your-endpoint.example.com';
    default:              return '';
  }
}

function defaultCustomTemplate() {
  return JSON.stringify(
    {
      method: 'POST',
      path: '/chat',
      headers: { 'content-type': 'application/json' },
      body: {
        model: '{{model}}',
        messages: '{{messages_json}}',
        stream: false,
      },
    },
    null,
    2,
  );
}

function defaultResponsePaths() {
  return JSON.stringify(
    {
      text: 'output.text',
      tool_call_name: 'output.tool_call.name',
      tool_call_args: 'output.tool_call.arguments',
      usage_prompt: 'usage.prompt_tokens',
      usage_completion: 'usage.completion_tokens',
    },
    null,
    2,
  );
}

export default function ConnectorDialog({
  open,
  initial,            // ModelConnectorPublic or null
  templates = [],
  onClose,
  onSubmit,           // (payload) => Promise
}) {
  const isEdit = !!initial?.id;
  const [provider, setProvider] = useState(initial?.provider || 'openai_compat');
  const [name, setName] = useState(initial?.name || '');
  const [baseUrl, setBaseUrl] = useState(initial?.base_url || defaultBaseUrlFor('openai_compat'));
  const [apiKey, setApiKey] = useState(''); // never pre-populated; preview is shown elsewhere
  const [apiKeyPreview, setApiKeyPreview] = useState(initial?.api_key_preview || '');
  const [authType, setAuthType] = useState(initial?.auth_type || 'bearer');
  const [authHeaderName, setAuthHeaderName] = useState(initial?.auth_header_name || '');
  const [defaultModel, setDefaultModel] = useState(initial?.default_model || '');
  const [models, setModels] = useState((initial?.models || []).join('\n'));
  const [organizationId, setOrganizationId] = useState(initial?.organization_id || '');
  const [projectId, setProjectId] = useState(initial?.project_id || '');
  const [apiVersion, setApiVersion] = useState(initial?.api_version || '');
  const [customHeadersText, setCustomHeadersText] = useState(
    JSON.stringify(initial?.custom_headers || {}, null, 2),
  );
  const [capabilitiesText, setCapabilitiesText] = useState(
    JSON.stringify(initial?.capabilities || {}, null, 2),
  );
  const [settingsText, setSettingsText] = useState(
    JSON.stringify(initial?.settings || {}, null, 2),
  );
  const [isEnabled, setIsEnabled] = useState(initial?.is_enabled ?? true);
  const [isAdmin, setIsAdmin] = useState(initial?.is_admin ?? false);
  const [isFavorite, setIsFavorite] = useState(initial?.is_favorite ?? false);
  const [groupName, setGroupName] = useState(initial?.group_name || '');
  const [tagsText, setTagsText] = useState((initial?.tags || []).join(', '));
  const [requestTemplate, setRequestTemplate] = useState(
    initial?.custom_headers?.request_template
      ? JSON.stringify(initial.custom_headers.request_template, null, 2)
      : defaultCustomTemplate(),
  );
  const [responsePaths, setResponsePaths] = useState(
    initial?.custom_headers?.response_paths
      ? JSON.stringify(initial.custom_headers.response_paths, null, 2)
      : defaultResponsePaths(),
  );
  const [revealKey, setRevealKey] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [showTest, setShowTest] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // If we don't have an initial row, seed from the canned template.
  useEffect(() => {
    if (initial) return;
    const tpl = templates.find((t) => t.provider === provider);
    if (tpl?.default_base_url) setBaseUrl(tpl.default_base_url);
  }, [provider, initial, templates]);

  // Switching provider on create: reset base URL to the new default.
  useEffect(() => {
    if (!initial) setBaseUrl(defaultBaseUrlFor(provider));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const customHeadersJson = useMemo(() => {
    try {
      return customHeadersText.trim() ? JSON.parse(customHeadersText) : {};
    } catch {
      return { __invalid: true, __raw: customHeadersText };
    }
  }, [customHeadersText]);

  const capabilitiesJson = useMemo(() => {
    try {
      return capabilitiesText.trim() ? JSON.parse(capabilitiesText) : {};
    } catch {
      return { __invalid: true };
    }
  }, [capabilitiesText]);

  const settingsJson = useMemo(() => {
    try {
      return settingsText.trim() ? JSON.parse(settingsText) : {};
    } catch {
      return { __invalid: true };
    }
  }, [settingsText]);

  const payload = useMemo(() => {
    const tags = tagsText
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
    const base = {
      name: name.trim(),
      provider,
      base_url: baseUrl.trim(),
      auth_type: authType,
      default_model: defaultModel.trim(),
      models: models.split('\n').map((s) => s.trim()).filter(Boolean),
      is_enabled: isEnabled,
      is_admin: isAdmin,
      is_favorite: isFavorite,
      group_name: groupName.trim() || null,
      tags,
    };
    if (authType === 'header' && authHeaderName.trim()) {
      base.auth_header_name = authHeaderName.trim();
    }
    if (organizationId.trim()) base.organization_id = organizationId.trim();
    if (projectId.trim()) base.project_id = projectId.trim();
    if (apiVersion.trim()) base.api_version = apiVersion.trim();
    if (!customHeadersJson.__invalid) base.custom_headers = customHeadersJson;
    if (!capabilitiesJson.__invalid) base.capabilities = capabilitiesJson;
    if (!settingsJson.__invalid) base.settings = settingsJson;
    // Always send the key field — backend treats empty as "no change" on PATCH.
    base.api_key = apiKey;
    // For the custom provider, attach the request/response template.
    if (provider === 'custom') {
      let rt, rp;
      try { rt = JSON.parse(requestTemplate); } catch { rt = null; }
      try { rp = JSON.parse(responsePaths); } catch { rp = null; }
      base.custom_headers = {
        ...(base.custom_headers || {}),
        request_template: rt,
        response_paths: rp,
      };
    }
    return base;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    name, provider, baseUrl, authType, authHeaderName,
    organizationId, projectId, apiVersion,
    defaultModel, models, customHeadersJson, capabilitiesJson, settingsJson,
    isEnabled, isAdmin, isFavorite, groupName, tagsText, apiKey,
    requestTemplate, responsePaths,
  ]);

  const submit = async (e) => {
    e?.preventDefault?.();
    setSubmitting(true);
    setError(null);
    try {
      // PATCH /api/connectors/{id} uses ModelConnectorUpdate, which
      // deliberately omits `provider` (immutable after create) and
      // `is_admin` (create-only, admin-gated). Stripping here keeps
      // the create and edit forms in sync without forking the
      // payload builder. The custom-request-template fields below
      // `provider` are only meaningful at create time too, so they
      // ride along on the same condition.
      const body = isEdit
        ? (() => {
            const { provider: _p, is_admin: _a, ...rest } = payload;
            return rest;
          })()
        : payload;
      await onSubmit(body);
      onClose();
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      size="xl"
      title={isEdit ? 'Edit connector' : 'Add connector'}
      description={
        isEdit
          ? 'Update the connection details and capabilities.'
          : 'Register an AI provider so Athena can route requests to it.'
      }
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => setShowTest((v) => !v)}
            disabled={!baseUrl.trim() || !provider}
          >
            <Plug size={14} strokeWidth={1.75} />
            {showTest ? 'Hide test' : 'Test before saving'}
          </Button>
          <div className="flex items-center gap-2 ml-auto">
            <Button variant="ghost" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={submit}
              disabled={submitting || !name.trim() || !baseUrl.trim() || !defaultModel.trim()}
            >
              {submitting ? (
                <>
                  <Loader2 size={14} strokeWidth={1.75} className="animate-spin" />
                  Saving…
                </>
              ) : isEdit ? 'Save changes' : 'Create connector'}
            </Button>
          </div>
        </>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Provider" hint={isEdit ? 'cannot change' : undefined}>
            <Select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={isEdit}>
              {ALL_PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Display name">
            <Input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              placeholder="e.g. My OpenAI account"
            />
          </Field>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Base URL">
            <Input
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              required
              spellCheck={false}
            />
          </Field>
          <Field label="Default model">
            <Input
              type="text"
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              required
              spellCheck={false}
              placeholder="e.g. gpt-4o-mini"
            />
          </Field>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Auth type">
            <Select value={authType} onChange={(e) => setAuthType(e.target.value)}>
              {AUTH_TYPES.map((a) => (
                <option key={a.value} value={a.value}>{a.label}</option>
              ))}
            </Select>
          </Field>
          {authType === 'header' && (
            <Field label="Header name">
              <Input
                type="text"
                value={authHeaderName}
                onChange={(e) => setAuthHeaderName(e.target.value)}
                placeholder="x-api-key"
              />
            </Field>
          )}
        </div>

        <Field
          label="API key"
          hint={isEdit ? 'leave blank to keep the existing key' : undefined}
        >
          <div className="relative">
            <Input
              type={revealKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={isEdit ? '(unchanged)' : 'sk-...'}
              autoComplete="off"
              spellCheck={false}
              className="pr-20"
            />
            <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-1">
              {apiKeyPreview && !apiKey && (
                <code className="text-[10px] text-ink-faint font-mono mr-1">{apiKeyPreview}</code>
              )}
              <button
                type="button"
                onClick={() => setRevealKey((v) => !v)}
                aria-label={revealKey ? 'Hide key' : 'Reveal key'}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-faint hover:text-ink hover:bg-surface-2 transition-colors"
              >
                {revealKey ? <EyeOff size={14} strokeWidth={1.75} /> : <Eye size={14} strokeWidth={1.75} />}
              </button>
            </div>
          </div>
        </Field>

        {(provider === 'openai_compat' || provider === 'azure_openai') && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {provider === 'openai_compat' && (
              <>
                <Field label="Organization ID" hint="optional">
                  <Input
                    type="text"
                    value={organizationId}
                    onChange={(e) => setOrganizationId(e.target.value)}
                  />
                </Field>
                <Field label="Project ID" hint="optional">
                  <Input
                    type="text"
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                  />
                </Field>
              </>
            )}
            {provider === 'azure_openai' && (
              <Field label="API version">
                <Input
                  type="text"
                  value={apiVersion}
                  onChange={(e) => setApiVersion(e.target.value)}
                  placeholder="2024-02-01"
                />
              </Field>
            )}
          </div>
        )}

        <Field label="Exposed models" hint="one per line">
          <Textarea
            value={models}
            onChange={(e) => setModels(e.target.value)}
            rows={3}
            placeholder="gpt-4o-mini&#10;gpt-4o"
          />
        </Field>

        {provider === 'custom' && (
          <div className="grid grid-cols-1 gap-4">
            <Field label="Request template" hint="JSON">
              <Textarea
                value={requestTemplate}
                onChange={(e) => setRequestTemplate(e.target.value)}
                rows={10}
                className="font-mono text-xs"
              />
            </Field>
            <Field label="Response paths" hint="JSON">
              <Textarea
                value={responsePaths}
                onChange={(e) => setResponsePaths(e.target.value)}
                rows={6}
                className="font-mono text-xs"
              />
            </Field>
          </div>
        )}

        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="flex items-center gap-1.5 self-start text-xs font-medium text-ink-dim hover:text-ink transition-colors"
        >
          <ChevronRight
            size={12}
            strokeWidth={1.75}
            className={`transition-transform ${showAdvanced ? 'rotate-90' : ''}`}
          />
          Advanced
        </button>
        {showAdvanced && (
          <div className="rounded-lg border border-hairline bg-surface-2/30 p-4 flex flex-col gap-4">
            <Field label="Custom headers" hint="JSON">
              <Textarea
                value={customHeadersText}
                onChange={(e) => setCustomHeadersText(e.target.value)}
                rows={3}
                className="font-mono text-xs"
              />
            </Field>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Capabilities" hint="JSON">
                <Textarea
                  value={capabilitiesText}
                  onChange={(e) => setCapabilitiesText(e.target.value)}
                  rows={3}
                  className="font-mono text-xs"
                />
              </Field>
              <Field label="Settings" hint="JSON">
                <Textarea
                  value={settingsText}
                  onChange={(e) => setSettingsText(e.target.value)}
                  rows={3}
                  className="font-mono text-xs"
                />
              </Field>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Field label="Group" hint="optional">
                <Input
                  type="text"
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                />
              </Field>
              <Field label="Tags" hint="comma separated">
                <Input
                  type="text"
                  value={tagsText}
                  onChange={(e) => setTagsText(e.target.value)}
                />
              </Field>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
              <Checkbox label="Enabled" checked={isEnabled} onChange={setIsEnabled} />
              <Checkbox label="Favorite" checked={isFavorite} onChange={setIsFavorite} />
              <Checkbox
                label="Shared (admin — visible to all users)"
                checked={isAdmin}
                onChange={setIsAdmin}
              />
            </div>
          </div>
        )}

        {error && (
          <div
            role="alert"
            className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-bg)]/60 px-3 py-2 text-sm text-[var(--danger)] flex items-start gap-2"
          >
            <AlertCircle size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
            <span className="flex-1 break-words">{error}</span>
          </div>
        )}

        {showTest && (
          <div className="rounded-lg border border-dashed border-hairline p-4 bg-surface-2/30">
            <TestPanel payload={payload} />
          </div>
        )}
      </form>
    </Dialog>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[10px] font-medium uppercase tracking-wider text-ink-dim">
        {label}
        {hint && <span className="ml-1 text-ink-faint normal-case tracking-normal">· {hint}</span>}
      </span>
      {children}
    </label>
  );
}

function Checkbox({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-2 text-sm text-ink cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border border-hairline text-[var(--accent)] focus:ring-hairline-strong"
      />
      {label}
    </label>
  );
}

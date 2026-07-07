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
import TestPanel from './TestPanel.jsx';

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

  if (!open) return null;

  const submit = async (e) => {
    e?.preventDefault?.();
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(payload);
      onClose();
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal modal-large"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? 'Edit connector' : 'Add connector'}
      >
        <div className="modal-header">
          <h2>{isEdit ? 'Edit connector' : 'Add connector'}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <form onSubmit={submit} className="connector-form">
          <div className="form-row">
            <label>
              <span>Provider</span>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                disabled={isEdit}
              >
                {ALL_PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Display name</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                placeholder="e.g. My OpenAI account"
              />
            </label>
          </div>

          <div className="form-row">
            <label>
              <span>Base URL</span>
              <input
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                required
                spellCheck={false}
              />
            </label>
            <label>
              <span>Default model</span>
              <input
                type="text"
                value={defaultModel}
                onChange={(e) => setDefaultModel(e.target.value)}
                required
                spellCheck={false}
                placeholder="e.g. gpt-4o-mini"
              />
            </label>
          </div>

          <div className="form-row">
            <label>
              <span>Auth type</span>
              <select value={authType} onChange={(e) => setAuthType(e.target.value)}>
                {AUTH_TYPES.map((a) => (
                  <option key={a.value} value={a.value}>{a.label}</option>
                ))}
              </select>
            </label>
            {authType === 'header' && (
              <label>
                <span>Header name</span>
                <input
                  type="text"
                  value={authHeaderName}
                  onChange={(e) => setAuthHeaderName(e.target.value)}
                  placeholder="x-api-key"
                />
              </label>
            )}
          </div>

          <div className="form-row">
            <label className="form-key-field">
              <span>API key</span>
              <input
                type={revealKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={
                  isEdit
                    ? '(unchanged — leave blank to keep the existing key)'
                    : 'sk-...'
                }
                autoComplete="off"
                spellCheck={false}
              />
              <div className="form-key-hint">
                {apiKeyPreview && !apiKey && (
                  <span>Current: <code>{apiKeyPreview}</code></span>
                )}
                <button
                  type="button"
                  className="form-link"
                  onClick={() => setRevealKey((v) => !v)}
                >
                  {revealKey ? 'Hide' : 'Reveal'}
                </button>
              </div>
            </label>
          </div>

          {(provider === 'openai_compat' || provider === 'azure_openai') && (
            <div className="form-row">
              {provider === 'openai_compat' && (
                <>
                  <label>
                    <span>Organization ID (optional)</span>
                    <input
                      type="text"
                      value={organizationId}
                      onChange={(e) => setOrganizationId(e.target.value)}
                    />
                  </label>
                  <label>
                    <span>Project ID (optional)</span>
                    <input
                      type="text"
                      value={projectId}
                      onChange={(e) => setProjectId(e.target.value)}
                    />
                  </label>
                </>
              )}
              {provider === 'azure_openai' && (
                <label>
                  <span>API version</span>
                  <input
                    type="text"
                    value={apiVersion}
                    onChange={(e) => setApiVersion(e.target.value)}
                    placeholder="2024-02-01"
                  />
                </label>
              )}
            </div>
          )}

          <div className="form-row">
            <label>
              <span>Exposed models (one per line)</span>
              <textarea
                value={models}
                onChange={(e) => setModels(e.target.value)}
                rows={3}
                placeholder="gpt-4o-mini&#10;gpt-4o"
              />
            </label>
          </div>

          {provider === 'custom' && (
            <>
              <div className="form-row">
                <label>
                  <span>Request template (JSON)</span>
                  <textarea
                    value={requestTemplate}
                    onChange={(e) => setRequestTemplate(e.target.value)}
                    rows={10}
                    className="form-code"
                  />
                </label>
              </div>
              <div className="form-row">
                <label>
                  <span>Response paths (JSON)</span>
                  <textarea
                    value={responsePaths}
                    onChange={(e) => setResponsePaths(e.target.value)}
                    rows={6}
                    className="form-code"
                  />
                </label>
              </div>
            </>
          )}

          <details className="form-advanced">
            <summary>Advanced</summary>
            <div className="form-row">
              <label>
                <span>Custom headers (JSON)</span>
                <textarea
                  value={customHeadersText}
                  onChange={(e) => setCustomHeadersText(e.target.value)}
                  rows={3}
                  className="form-code"
                />
              </label>
            </div>
            <div className="form-row">
              <label>
                <span>Capabilities (JSON)</span>
                <textarea
                  value={capabilitiesText}
                  onChange={(e) => setCapabilitiesText(e.target.value)}
                  rows={3}
                  className="form-code"
                />
              </label>
              <label>
                <span>Settings (JSON)</span>
                <textarea
                  value={settingsText}
                  onChange={(e) => setSettingsText(e.target.value)}
                  rows={3}
                  className="form-code"
                />
              </label>
            </div>
            <div className="form-row">
              <label>
                <span>Group (optional)</span>
                <input
                  type="text"
                  value={groupName}
                  onChange={(e) => setGroupName(e.target.value)}
                />
              </label>
              <label>
                <span>Tags (comma separated)</span>
                <input
                  type="text"
                  value={tagsText}
                  onChange={(e) => setTagsText(e.target.value)}
                />
              </label>
            </div>
            <div className="form-row form-row-checks">
              <label className="form-check">
                <input
                  type="checkbox"
                  checked={isEnabled}
                  onChange={(e) => setIsEnabled(e.target.checked)}
                />
                <span>Enabled</span>
              </label>
              <label className="form-check">
                <input
                  type="checkbox"
                  checked={isFavorite}
                  onChange={(e) => setIsFavorite(e.target.checked)}
                />
                <span>Favorite</span>
              </label>
              <label className="form-check">
                <input
                  type="checkbox"
                  checked={isAdmin}
                  onChange={(e) => setIsAdmin(e.target.checked)}
                />
                <span>Shared (admin — visible to all users)</span>
              </label>
            </div>
          </details>

          {error && <div className="test-panel-error">⚠ {error}</div>}

          <div className="modal-footer">
            <button
              type="button"
              onClick={() => setShowTest((v) => !v)}
              disabled={!baseUrl.trim() || !provider}
            >
              {showTest ? 'Hide test' : 'Test before saving'}
            </button>
            <div className="modal-footer-right">
              <button type="button" onClick={onClose} disabled={submitting}>
                Cancel
              </button>
              <button type="submit" className="primary" disabled={submitting || !name.trim() || !baseUrl.trim() || !defaultModel.trim()}>
                {submitting ? 'Saving…' : isEdit ? 'Save changes' : 'Create connector'}
              </button>
            </div>
          </div>

          {showTest && (
            <div className="modal-test-section">
              <TestPanel payload={payload} />
            </div>
          )}
        </form>
      </div>
    </div>
  );
}

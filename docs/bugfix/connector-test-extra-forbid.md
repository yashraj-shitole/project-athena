# "Test before saving" 422: `body.is_enabled / is_admin / is_favorite / group_name / tags: Extra inputs are not permitted`

_Reported 2026-07-08: clicking "Test before saving" in the connector
create/edit dialog returned a 422 listing
`body.is_enabled`, `body.is_admin`, `body.is_favorite`, `body.group_name`,
`body.tags` as "Extra inputs are not permitted". Root cause + fix
adversarially verified by a 4-dimension Workflow (root-cause /
allowlist-exactness / regression-edges / security-intent, 4 agents, each
reading the real code incl. `TestRequest`, `RequestBase`, the `/test`
route, and the custom-provider adapter)._

## Root cause (confirmed)

The connector create/edit dialog (`ConnectorDialog.jsx`) builds a single
`payload` object (the `useMemo` at lines 171-211) that is shared between
two consumers:

1. `onSubmit(payload)` → `POST /api/connectors` (create) or
   `PATCH /api/connectors/{id}` (update), backed by `ModelConnectorCreate`
   / `ModelConnectorUpdate`.
2. `<TestPanel payload={payload} />` → `connectorService.test(payload)` →
   `POST /api/connectors/test`, backed by **`TestRequest`**
   (`app/schemas/connector.py:243`).

`TestRequest` is a **strict** schema: it inherits `extra="forbid"` from
`RequestBase` (`app/schemas/base.py:53`) and declares only the
connection-relevant fields a probe needs — `name, provider, base_url,
api_key, auth_type, auth_header_name, organization_id, project_id,
api_version, custom_headers, default_model, models, capabilities,
settings, timeout_s`. It deliberately does **not** declare the
ownership / visibility flags.

But the shared `payload` always includes `is_enabled`, `is_admin`,
`is_favorite`, `group_name`, `tags` (lines 183-187) — fields that exist
on `ModelConnectorCreate`/`Update` but **not** on `TestRequest`. So the
`extra="forbid"` guard rejected them with a 422 before the route ran.

The live-connector branch in `TestPanel.jsx` (lines 29-40) was already
correct: it hand-picks only `TestRequest`-compatible keys. Only the
`payload` branch (line 41) forwarded the whole object unfiltered — which
is why the symptom appeared only on the "Test before saving" path from
the dialog, not when probing an already-saved connector.

## Why the fix is on the frontend (not the backend)

`TestRequest`'s docstring (`connector.py:246-249`) is explicit:
`extra="forbid"` is the defense against a payload smuggling `is_admin`
(or any undeclared field) into the route. `RequestBase` (`base.py:37-53`)
calls `extra="forbid"` the primary defense against mass-assignment
(H-22). `ModelConnectorUpdate` deliberately **omits** `is_admin` from
PATCH (`connector.py:117-130`) because the `connectors_iso` RLS policy
is `user_id = me OR is_admin` — flipping the flag promotes a row into
global visibility, so the lever is removed from the schema.

The `/test` route never persists anything (`connectors.py:583-633`) and
never reads `is_admin`/`is_enabled`/`is_favorite`/`group_name`/`tags`, so
smuggling them is *currently* harmless. But "currently harmless" is
exactly the future the forbid defends against: a refactor that reuses
`TestRequest` as the base for a persisting route, or a `/test-and-save`
variant, would silently turn a smuggled `is_admin` into a privilege
escalation. The forbid makes that a 422, not an escalation. A connection
probe has no legitimate use for any of the 5 flags anyway (you test
*before* enabling; visibility is a saved-state concept). So the frontend
must send exactly the `TestRequest`-declared fields, and the backend
stays forbidding. Relaxing the backend (`extra="ignore"` or accepting
the 5 fields) would erode the H-22 defense, contradict the docstrings,
and normalize the same `is_admin` lever `ModelConnectorUpdate` removed.

## Fix

`frontend/src/components/connectors/TestPanel.jsx` — add an allowlist
mapper that picks **exactly** the 15 `TestRequest` fields from the
`payload` before posting, and apply it on the `payload` branch only:

```js
const TEST_FIELDS = [
  'name', 'provider', 'base_url', 'api_key', 'auth_type',
  'auth_header_name', 'organization_id', 'project_id', 'api_version',
  'custom_headers', 'default_model', 'models', 'capabilities',
  'settings', 'timeout_s',
];

function toTestPayload(payload) {
  const src = payload || {};
  const out = {};
  for (const k of TEST_FIELDS) if (k in src) out[k] = src[k];
  return out;
}
// …
: await connectorService.test(toTestPayload(payload));
```

The live `connector` branch is unchanged.

### Implementation notes (from the adversarial review)

- **Key-membership, not truthiness.** `if (k in src)` copies the value
  verbatim, including empty string and `null`. A truthiness filter would
  drop `default_model: ""` (legitimate — `TestRequest.default_model`
  has no `min_length`, and "Test before saving" is enabled before the
  Save button which gates on a non-empty model) and, for the `custom`
  provider, would drop a `null` `request_template` nested in
  `custom_headers`, surfacing a confusing "CustomProvider requires
  request_template" instead of the underlying JSON-parse error.
- **Null-guard.** `src = payload || {}` so a future caller passing
  `payload={null}` gets a clean empty payload instead of a `TypeError`.
- **`custom_headers` is a single allowlist key.** Its nested
  `request_template` / `response_paths` survive the top-level filter
  intact; the backend reads them at `services/providers/custom.py:173-178`.
- **`timeout_s` is not in the dialog payload** → omitted → backend
  default `8.0` applies.

## Verification

- Adversarial Workflow (4 dimensions) → all **CONFIRMED** (the
  "regression" dimension's `PARTIAL` only restates that the
  test-before-save path was already broken — i.e. the bug itself; the
  fix resolves it, it does not introduce a regression).
- Frontend `npm run build` → `✓ built in 3.86s`, exit 0 (only the
  pre-existing >500 kB chunk-size warning).
- Backend `pytest tests/test_connectors_api.py -k "schema_rejects_extra
  or is_admin"` → **3 passed** (`ModelConnectorCreate` rejects extra
  fields, `ModelConnectorUpdate` rejects extra fields, `is_admin` is
  absent from `ModelConnectorUpdate`). The strict-schema defense is
  intact and unchanged.

## Known pre-existing caveat (not introduced here, not fixed here)

`TestRequest.custom_headers` (and `ModelConnectorCreate.custom_headers`)
is annotated `dict[str, str]`, but the `custom` provider stores **nested
dicts** there (`request_template` / `response_paths` are objects). Under
Pydantic v2 lax mode a `dict` value is not coerced to `str`, so a
custom-provider create *or* test request with nested template objects
could be rejected at schema validation. This is pre-existing (affects
the save path too) and independent of the 5-field 422 the user reported
(the reported error listed only the extra fields, so the user was not
hitting this). Filed here for a follow-up; out of scope for this fix.

## Files changed
- `frontend/src/components/connectors/TestPanel.jsx` — added
  `TEST_FIELDS` allowlist + `toTestPayload()` mapper; applied on the
  `payload` branch of `run()`. Live `connector` branch unchanged.

## To apply
Rebuild the frontend (`docker compose up -d --build` rebuilds the `web`/
`nginx` stages) or, for local dev, Vite HMR picks it up. No backend
change, no DB migration.
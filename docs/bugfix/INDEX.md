# Bug & Vulnerability Index

_61 confirmed findings across 9 dimensions. Sorted by severity then dimension then id._

| # | Sev | Dimension | ID | Location | Status |
|---|---|---|---|---|---|
| 1 | CRITICAL | infra-secrets | [`jwt-secret-default-shipped`](./infra-secrets.md) | `infra/docker-compose.yml:111` | Fixed |
| 2 | CRITICAL | infra-secrets | [`postgres-default-creds`](./infra-secrets.md) | `infra/docker-compose.yml:27` | Fixed |
| 3 | CRITICAL | infra-secrets | [`rls-ineffective-owner-bypass`](./infra-secrets.md) | `infra/init.sql:129` | Fixed |
| 4 | CRITICAL | rls-isolation | [`rls-guc-set-from-caller-user-id`](./rls-isolation.md) | `backend/app/services/retrieval/search.py:66` | Fixed |
| 5 | CRITICAL | rls-isolation | [`tools-invoke-open-to-all-users`](./rls-isolation.md) | `backend/app/api/tools.py:101` | Fixed |
| 6 | CRITICAL | rls-isolation | [`tools-upsert-cross-tenant-hijack`](./rls-isolation.md) | `backend/app/api/tools.py:32` | Fixed |
| 7 | HIGH | auth-jwt | [`default-jwt-secret-no-fail-fast`](./auth-jwt.md) | `backend/app/core/config.py:74` | Fixed |
| 8 | HIGH | auth-jwt | [`login-json-missing-is-active-check`](./auth-jwt.md) | `backend/app/api/auth.py:95` | Fixed |
| 9 | HIGH | frontend | [`token-localstorage-xss-theft`](./frontend.md) | `frontend/src/services/apiClient.js:17` | Mitigated |
| 10 | HIGH | infra-secrets | [`multipart-redos-cve`](./infra-secrets.md) | `backend/requirements.txt:4` | Fixed |
| 11 | HIGH | infra-secrets | [`no-tls-nginx`](./infra-secrets.md) | `infra/nginx-prod.conf:20` | Fixed |
| 12 | HIGH | infra-secrets | [`ollama-port-exposed`](./infra-secrets.md) | `infra/docker-compose.yml:62` | Fixed |
| 13 | HIGH | infra-secrets | [`postgres-port-exposed`](./infra-secrets.md) | `infra/docker-compose.yml:31` | Fixed |
| 14 | HIGH | orchestrator-logic | [`llm-complete-swallows-ollama-error`](./orchestrator-logic.md) | `backend/app/services/orchestrator/llm_client.py:82` | Fixed |
| 15 | HIGH | orchestrator-logic | [`stream-duplicate-text-message-start`](./orchestrator-logic.md) | `backend/app/services/orchestrator/agent.py:459` | Fixed |
| 16 | HIGH | rls-isolation | [`mcp-attach-ssrf`](./rls-isolation.md) | `backend/app/api/tools.py:83` | Fixed |
| 17 | HIGH | rls-isolation | [`rls-not-forced-owner-bypass`](./rls-isolation.md) | `infra/init.sql:129` | Fixed |
| 18 | HIGH | rls-isolation | [`tools-set-enabled-cross-tenant`](./rls-isolation.md) | `backend/app/api/tools.py:59` | Fixed |
| 19 | HIGH | ssrf-mcp-tools | [`ssrf-http-handler-arbitrary-url`](./ssrf-mcp-tools.md) | `backend/app/tools/registry.py:130` | Fixed |
| 20 | HIGH | ssrf-mcp-tools | [`ssrf-mcp-attach-no-validation`](./ssrf-mcp-tools.md) | `backend/app/api/tools.py:83` | Fixed |
| 21 | HIGH | ssrf-mcp-tools | [`tool-no-ownership-authz-bypass`](./ssrf-mcp-tools.md) | `backend/app/api/tools.py:59` | Fixed |
| 22 | HIGH | ssrf-mcp-tools | [`tool-upsert-overwrites-builtin`](./ssrf-mcp-tools.md) | `backend/app/tools/registry.py:203` | Fixed |
| 23 | MEDIUM | async-cache-db | [`blocking-encode-in-async`](./async-cache-db.md) | `backend/app/services/retrieval/hybrid.py:82` | Fixed |
| 24 | MEDIUM | async-cache-db | [`cache-no-fail-open-redis-outage`](./async-cache-db.md) | `backend/app/services/retrieval/search.py:72` | Fixed |
| 25 | MEDIUM | auth-jwt | [`refresh-token-reuse-no-rotation-revocation`](./auth-jwt.md) | `backend/app/api/auth.py:108` | Fixed |
| 26 | MEDIUM | frontend | [`stream-bypasses-401-handling`](./frontend.md) | `frontend/src/services/apiClient.js:197` | Fixed |
| 27 | MEDIUM | infra-secrets | [`api-port-direct-exposed`](./infra-secrets.md) | `infra/docker-compose.yml:118` | Fixed |
| 28 | MEDIUM | infra-secrets | [`containers-run-as-root`](./infra-secrets.md) | `infra/entrypoint-api.sh:1` | Fixed |
| 29 | MEDIUM | infra-secrets | [`fastapi-starlette-redos-cve`](./infra-secrets.md) | `backend/requirements.txt:2` | Fixed |
| 30 | MEDIUM | infra-secrets | [`metrics-model-exposed-unauth`](./infra-secrets.md) | `infra/nginx-prod.conf:58` | Fixed |
| 31 | MEDIUM | infra-secrets | [`missing-security-headers`](./infra-secrets.md) | `infra/nginx-prod.conf:19` | Fixed |
| 32 | MEDIUM | infra-secrets | [`unpinned-ml-deps`](./infra-secrets.md) | `backend/requirements.txt:42` | Verified |
| 33 | MEDIUM | orchestrator-logic | [`retry-ignores-corrected-tool-name`](./orchestrator-logic.md) | `backend/app/services/orchestrator/agent.py:264` | Fixed |
| 34 | MEDIUM | orchestrator-logic | [`stream-tool-failure-text-not-streamed`](./orchestrator-logic.md) | `backend/app/services/orchestrator/agent.py:452` | Fixed |
| 35 | MEDIUM | orchestrator-logic | [`token-budget-answer-not-reserved`](./orchestrator-logic.md) | `backend/app/services/orchestrator/prompter.py:102` | Fixed |
| 36 | MEDIUM | retrieval-injection | [`bigram-generation-dead`](./retrieval-injection.md) | `backend/app/services/ingestion/keywords.py:66` | Fixed |
| 37 | MEDIUM | retrieval-injection | [`cache-key-omits-top-k`](./retrieval-injection.md) | `backend/app/services/retrieval/search.py:38` | Fixed |
| 38 | MEDIUM | rls-isolation | [`dual-dbsession-rls-footgun`](./rls-isolation.md) | `backend/app/core/database.py:88` | Fixed |
| 39 | MEDIUM | ssrf-mcp-tools | [`internal-handler-arbitrary-callable`](./ssrf-mcp-tools.md) | `backend/app/tools/registry.py:120` | Fixed |
| 40 | MEDIUM | upload-ingestion | [`doc-extension-no-extractor`](./upload-ingestion.md) | `backend/app/services/ingestion/extractors.py:110` | Fixed |
| 41 | MEDIUM | upload-ingestion | [`extension-only-allowlist-no-content-validation`](./upload-ingestion.md) | `backend/app/api/documents.py:51` | Fixed |
| 42 | MEDIUM | upload-ingestion | [`file-written-before-commit-orphan-on-failure`](./upload-ingestion.md) | `backend/app/api/documents.py:64` | Fixed |
| 43 | MEDIUM | upload-ingestion | [`no-page-row-or-chunk-cap-resource-exhaustion`](./upload-ingestion.md) | `backend/app/services/ingestion/extractors.py:73` | Fixed |
| 44 | MEDIUM | upload-ingestion | [`upload-body-not-capped-before-buffering`](./upload-ingestion.md) | `backend/app/api/documents.py:63` | Fixed |
| 45 | LOW | async-cache-db | [`reset-rls-swallows-all-exceptions`](./async-cache-db.md) | `backend/app/core/database.py:71` | Fixed |
| 46 | LOW | auth-jwt | [`bcrypt-72-byte-truncation-collision`](./auth-jwt.md) | `backend/app/core/security.py:21` | Fixed |
| 47 | LOW | auth-jwt | [`login-reveals-inactive-status`](./auth-jwt.md) | `backend/app/api/auth.py:88` | Fixed |
| 48 | LOW | auth-jwt | [`login-timing-email-enumeration`](./auth-jwt.md) | `backend/app/api/auth.py:80` | Fixed |
| 49 | LOW | auth-jwt | [`register-email-enumeration`](./auth-jwt.md) | `backend/app/api/auth.py:62` | Fixed |
| 50 | LOW | frontend | [`timeout-signal-timer-leak`](./frontend.md) | `frontend/src/services/apiClient.js:99` | Fixed |
| 51 | LOW | infra-secrets | [`cors-localhost-default-prod`](./infra-secrets.md) | `infra/docker-compose.yml:114` | Fixed |
| 52 | LOW | infra-secrets | [`secrets-in-plaintext-env`](./infra-secrets.md) | `infra/docker-compose.yml:105` | Mitigated |
| 53 | LOW | infra-secrets | [`unpinned-image-tags`](./infra-secrets.md) | `infra/docker-compose.yml:59` | Fixed |
| 54 | LOW | orchestrator-logic | [`coerce-arguments-raw-fallback`](./orchestrator-logic.md) | `backend/app/services/orchestrator/tool_call.py:93` | Fixed |
| 55 | LOW | orchestrator-logic | [`prompt-injection-via-chunks`](./orchestrator-logic.md) | `backend/app/services/orchestrator/prompter.py:124` | Fixed |
| 56 | LOW | retrieval-injection | [`vector-dim-not-validated`](./retrieval-injection.md) | `backend/app/services/retrieval/vector.py:27` | Fixed |
| 57 | LOW | ssrf-mcp-tools | [`mcp-jsonrpc-shape-unvalidated`](./ssrf-mcp-tools.md) | `backend/app/tools/mcp.py:79` | Fixed |
| 58 | LOW | upload-ingestion | [`background-ingest-errors-swallowed`](./upload-ingestion.md) | `backend/app/api/documents.py:101` | Fixed |
| 59 | LOW | upload-ingestion | [`csv-text-ingest-accepts-binary`](./upload-ingestion.md) | `backend/app/services/ingestion/extractors.py:73` | Fixed |
| 60 | LOW | upload-ingestion | [`pdf-no-page-cap-decompression`](./upload-ingestion.md) | `backend/app/services/ingestion/extractors.py:38` | Fixed |
| 61 | INFO | retrieval-injection | [`dead-to-tsquery-helper`](./retrieval-injection.md) | `backend/app/services/retrieval/lexical.py:20` | Fixed |

## By dimension

- [Row-Level Security & Tenant Isolation](./rls-isolation.md) - 7 finding(s)
- [SSRF & MCP / Tool Handler Surface](./ssrf-mcp-tools.md) - 6 finding(s)
- [Authentication & JWT](./auth-jwt.md) - 7 finding(s)
- [Agent Orchestrator Logic](./orchestrator-logic.md) - 7 finding(s)
- [Upload & Ingestion Pipeline](./upload-ingestion.md) - 8 finding(s)
- [Retrieval & Prompt-Injection](./retrieval-injection.md) - 4 finding(s)
- [Async, Cache & Database Session Hygiene](./async-cache-db.md) - 3 finding(s)
- [Frontend (SPA / API Client)](./frontend.md) - 3 finding(s)
- [Infrastructure, Secrets & Deployment Hardening](./infra-secrets.md) - 16 finding(s)

## Companion audit: Frontend UI-State (separate pass)

A separate, later audit focused on **UI state not updating after actions / non-smooth UX**
(not security). It confirmed **19 unique findings (+2 second-lens duplicates, +3 rejected)**
across 5 lenses (auth-state-sync, chat-stream-reconcile, documents-ui, routing-navigation,
css-ux-smoothness) — all fixed. See [`frontend-ui-state.md`](./frontend-ui-state.md).
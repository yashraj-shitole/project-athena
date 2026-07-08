# Phase 2 roadmap

Phase 1 ships a working MVP. These are the most-valuable Phase 2 improvements, ordered by user-visible impact.

## 1. Token storage in `httpOnly` cookies (security)

Today the frontend stores JWTs in `localStorage`, which is XSS-vulnerable. Phase 2:

- Backend sets the access token in an `httpOnly; Secure; SameSite=Strict` cookie on login.
- A separate refresh token in a `httpOnly; Secure; SameSite=Strict` cookie.
- Frontend stops reading `localStorage`. `apiClient` becomes cookie-aware (`credentials: 'include'`).
- Logout: backend clears the cookies and revokes the refresh token in a server-side denylist.

This eliminates the entire class of XSS-driven token theft.

## 2. Account lockout / rate limiting (security)

- Per-IP and per-account rate limits on `/api/auth/login` and `/api/auth/refresh`.
- SlowAPI middleware (or `limits` + a Redis backend).
- Account lockout after N failed attempts (sliding window).

## 3. Streaming tool calls (UX, latency)

The orchestrator today does a non-streaming first pass purely to detect a tool call. Phase 2 streams both the tool-call JSON *and* the answer, parsing incrementally. This saves ~1s of latency on tool-using turns.

## 4. Full MCP support (extensibility)

`app/tools/mcp.py` is a minimal JSON-RPC client. Phase 2:

- `stdio` transport (for local MCP servers like the filesystem one).
- Streaming notifications (`notifications/tools/list_changed`).
- Sampling (let the MCP server call back into the LLM).
- Roots (declare which directories the MCP server may access).

## 5. Reranker (retrieval quality)

A second-stage reranker (cross-encoder, e.g. `cross-encoder/ms-marco-MiniLM-L-6-v2`) re-scores the top-K from RRF. `app/services/retrieval/rerank.py` is the placeholder.

## 6. Production observability

- OpenTelemetry traces for `chat` spans (auto-instrument `httpx`, `sqlalchemy`).
- A Prometheus `/metrics` exporter (the current `/metrics` is just hit/miss counters).
- A simple log dashboard (Loki + Grafana).

## 7. Persistent conversation titles

Today the conversation title is set to the first user message (truncated to 100 chars). Phase 2 uses the LLM to generate a 3-5 word title after the first turn.

## 8. Multi-modal document support

PDFs and images today become plain text. Phase 2 adds:

- Embedded image extraction from PDFs.
- A vision model for the extracted images.
- A separate `describe_image` tool that the orchestrator can call.

## 9. Conversation export

`GET /api/chat/conversations/{id}/export` → `application/json` (or `text/markdown`) with all messages and citations.

## 10. Frontend tests (regression safety)

- Vitest component tests for the auth singleton, the SSE hook, the document polling, and the chat store.
- Playwright end-to-end smoke for register → upload → chat.

## 11. Database migrations (Alembic)

`infra/init.sql` is fine for greenfield. For an environment where the schema changes, Alembic is the right tool. The dependency is already in `requirements.txt` but unused.

## 12. Container image hardening

- Multi-stage Dockerfile to slim the API image.
- Non-root user in the container.
- Trivy scan in CI.
- Drop capabilities; read-only root filesystem.

## 13. Backup / restore

`pg_dump` cron + S3 (or equivalent). Document and test the restore.

## 14. Horizontal scaling

The API is stateless and safe to run N replicas behind a load balancer. The only stateful piece is the database, which Postgres + read replicas can absorb. Phase 2 designs and tests that.

## Deferred indefinitely

- **Mobile app** — web is enough for now.
- **Plugin system** — too speculative.
- **Multi-tenant SaaS billing** — single-tenant per deployment is fine for Phase 2.

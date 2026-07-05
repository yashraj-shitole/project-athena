# Testing

The backend ships with two test tiers:

- **Unit tests** — fast, hermetic, no external services. Run by default.
- **Integration tests** — require Postgres + Redis + Ollama. Opt in with `--run-integration`.

The frontend has no automated tests in Phase 1 (manual smoke + Playwright is Phase 2).

## Running

```bash
cd backend
pip install -r requirements.txt

# Unit tests only (fast, ~1s)
pytest

# With integration
pytest --run-integration
```

`conftest.py` forces a test-friendly env (in-memory SQLite, per-process temp dir) so the unit suite never touches your real DB.

## What's covered

| File | Covers |
|---|---|
| `tests/test_security.py` | bcrypt hash/verify, JWT round-trip, decode error paths. |
| `tests/test_text.py` | `clean_text`, `count_tokens`, `truncate_tokens`. |
| `tests/test_tool_call.py` | `validate_arguments` (valid, invalid, schema error), `coerce_arguments`, `fallback_keywords`, `build_corrective_note`. |
| `tests/test_prompter.py` | `build_prompt` stays under the 3000-token budget with various chunk counts and history sizes; `extract_citations` parses `[chunk:<uuid>]` correctly. |
| `tests/test_integration.py` | Health endpoint shape. Skipped unless `--run-integration`. |

The prompter tests are the most valuable — they exercise the truncation order (system prompt → tools → history → chunks → query) and confirm we never exceed the budget.

## Integration tests

The integration suite hits:

- `GET /health` — confirms the JSON shape and presence of `db`, `redis`, `llm` keys.
- (Phase 2) full chat turn with a real LLM.
- (Phase 2) end-to-end document upload → index → retrieval → chat.

To enable:

```bash
# 1. Start the stack
cd infra && docker compose up -d
docker exec -it athena-ollama ollama pull qwen2.5:1.5b-instruct

# 2. Run with the flag
cd ../backend
pytest --run-integration -v
```

`conftest.py` checks for the `--run-integration` flag in `pytest_collection_modifyitems` and skips every test marked `@pytest.mark.integration` if it's not set.

## Frontend testing (Phase 2)

Recommended setup:

- **Vitest** for component tests (login form, SSE hook, document manager polling).
- **Playwright** for end-to-end smoke (register → upload → chat).
- **MSW** (mock service worker) for `fetch` / SSE in component tests.

Add when the team has bandwidth. The code is structured to be testable: `useChatStream` exposes its event types, `useAuth` is a singleton with a clean `setAuth`/`logout` surface, `chatStore` is a plain `zustand` store (no React, no router).

## Lint / type checks (Phase 2)

- Backend: `ruff check` + `mypy --strict` is the recommended combo. The codebase has no type stubs in Phase 1.
- Frontend: `eslint` + `@typescript-eslint`. Phase 1 ships without.

## CI

Recommended GitHub Actions matrix (Phase 2):

```yaml
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r backend/requirements.txt
      - run: pytest backend/tests
  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
      redis:
        image: redis:7
    steps:
      - uses: actions/checkout@v4
      - run: pip install -r backend/requirements.txt
      - run: pytest backend/tests --run-integration
```

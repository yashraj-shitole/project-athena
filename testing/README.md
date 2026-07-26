# Project Athena — Quality Assurance and LLM Evaluation Framework

> Cross-cutting test & eval layer for Project Athena. Lives alongside
> `backend/`, `frontend/`, `infra/`, and `docs/`. **Complements** the
> existing `backend/tests/` (hermetic unit tests stay there); this tree owns
> the layers that don't belong inside any one component: end-to-end flows,
> performance, security, and LLM-as-judge evaluations.

## What lives here

| Directory | Purpose |
|---|---|
| `workflows/smoke/` | Fast black-box tests against the running stack (`/health`, `/model`, `/metrics`, register/login, upload). Run on every PR. |
| `workflows/regression/` | Tests that reproduce documented bugs from `docs/bugfix/`. Gated; longer. |
| `workflows/integration/` | Multi-component: chat + retrieval + tool calling + streaming + connectors. |
| `workflows/e2e/` | Browser-level flows via Playwright (onboarding, upload-then-query, model picker). |
| `workflows/performance/` | Latency + throughput (pytest-benchmark, locust). |
| `workflows/security/` | Auth bypass, RBAC, SQL/XSS/prompt-injection, SSRF, upload validation, key encryption. |
| `workflows/accessibility/` | axe-core via Playwright. |
| `llm_evals/` | The LLM evaluation framework. Deterministic matchers + LLM-as-judge, datasets, scenarios, reports, baselines. |
| `fixtures/` | Sample documents, connector payloads, user/role seeds. |
| `mocks/` | `httpx.MockTransport` handlers, fake `ProviderAdapter`s, stub LLMs. |
| `scripts/` | Runner scripts (`run_smoke.sh`, `run_evals.sh`, etc.) — both `.ps1` and `.sh`. |
| `reports/` | Generated artifacts (HTML, JSON). `.gitignore`d. |
| `coverage/` | Coverage XML/HTML. `.gitignore`d. |
| `ci/` | GitHub Actions workflows + helpers. |

## Quick start

Every script in `scripts/` and at the repo root (when present) goes through
the same docker-compose entry point as the existing test runner. The pattern
mirrors `scripts/test.ps1` / `scripts/test.sh`:

```bash
# All hermetic suites
./scripts/run_all.sh

# Just the smoke suite (fastest, ~30s)
./scripts/run_smoke.sh

# LLM evaluation suite
./scripts/run_evals.sh
```

```powershell
.\scripts\run_smoke.ps1
.\scripts\run_evals.ps1
```

## Pytest markers

The `testing/pytest.ini` defines one marker per sub-area. The new directory
reuses the `--run-integration` flag from `backend/tests/conftest.py` (it
picks up the flag from `pytest_addoption` chain via parent conftest).

| Marker | Requires | What it covers |
|---|---|---|
| `smoke` | running stack (`/health` reachable) | public endpoint shape |
| `regression` | running stack | per-bug reproductions |
| `integration` | `--run-integration` + live DB/Redis/Ollama | multi-component |
| `e2e` | running stack + Playwright browser | full browser flows |
| `perf` | running stack | latency, throughput |
| `security` | running stack or hermetic | auth bypass, RBAC, etc. |
| `a11y` | running stack + Playwright | axe-core |
| `eval` | running stack + LLM judge | LLM quality |

The default run (`pytest` from `testing/`) executes `smoke` only. Use
`pytest -m "smoke or security"` to mix.

## Design rules

The new tree is **additive** — nothing in `backend/tests/` or the existing
runner breaks. The constraints it inherits from the codebase:

- **No breaking changes.** The existing `backend/tests/`, `scripts/test.ps1`, and
  `scripts/test.sh` keep working exactly as today.
- **Docker-first.** Tests run inside `api` and `web-dev` containers; no
  new top-level services.
- **Hermetic by default.** Live Postgres/Redis/Ollama are gated behind
  `--run-integration`, the same flag `backend/tests/conftest.py` already
  uses.
- **Secrets never checked in.** Datasets use `{{env:VAR}}` placeholders;
  CI injects real secrets via `vars:` / `secrets:`.
- **SSRF guard stays in the router**, not the adapter constructor (memory).
- **Live SQLAlchemy is integration-only** (memory).
- **All scripts dual-surface.** `*.ps1` (pwsh 7+) and `*.sh` (bash 4+),
  dot-sourcing the existing `scripts/goThrough/_helpers.{ps1,sh}`.
- **Use `Invoke-Compose` / `run_compose` for docker**, never bare
  `docker compose` (false-passes have happened in `scripts/test.ps1` history from
  this exact issue).

## Where do I add a new test?

| You want to test… | Put it in |
|---|---|
| A public endpoint shape (`/health`, `/model`, `/metrics`) | `workflows/smoke/test_<endpoint>.py` |
| A bug from `docs/bugfix/<dimension>.md` | `workflows/regression/test_<dimension>.py` |
| A multi-component flow (chat + retrieval + tools) | `workflows/integration/test_<flow>.py` |
| A user-visible browser flow | `workflows/e2e/test_<flow>.py` |
| Latency / throughput | `workflows/performance/test_<bench>.py` |
| A security invariant | `workflows/security/test_<class>.py` |
| A new LLM eval scenario | `llm_evals/scenarios/test_<dataset>.py` |
| A new deterministic dataset row | `llm_evals/datasets/<dataset>.jsonl` |
| A new LLM judge | `llm_evals/eval/judges.py` |

## See also

- `docs/testing.md` — backend unit + integration test reference.
- `docs/ci.md` — pipeline details.
- `docs/bugfix/` — the source-of-truth list of bugs the regression suite
  reproduces.
- `docs/architecture/orchestrator.md` — the contract LLM evals assert against.

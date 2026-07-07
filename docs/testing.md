# Testing

> Athena ships with two test surfaces:
>
> 1. **Backend unit + integration tests** — live in `backend/tests/`.
>    Fast, hermetic, and run on every PR.
> 2. **Cross-cutting QA and LLM evals** — live in [`/testing/`](../testing/README.md).
>    Smoke, security, performance, E2E, and a hand-rolled LLM evaluation
>    framework. Run by the GitHub Actions pipeline (see [ci.md](ci.md)).

## Running

### Backend (`backend/tests/`)

```bash
cd backend
pip install -r requirements.txt
pytest                          # unit only (hermetic; ~1s)
pytest --run-integration        # with live Postgres/Redis/Ollama
```

The conftest forces hermetic env vars (in-memory SQLite, per-process
temp dir) so the unit suite never touches your real DB. See
[`backend/tests/conftest.py`](../backend/tests/conftest.py).

### Cross-cutting (`/testing/`)

```bash
cd testing
pip install -r ../backend/requirements.txt

pytest                          # smoke only by default (in pytest.ini)
pytest -m "smoke or security"   # mixed
pytest -m "eval and not slow"   # LLM evals (Ollama judge)
pytest -m "perf and not integration"  # performance benchmarks
pytest -m "e2e"                 # Playwright E2E (skipped if Playwright not installed)
pytest -m "integration" --run-integration  # multi-component
```

Or use the runner scripts (mirrors `test.ps1` / `test.sh`):

```bash
./scripts/run_smoke.sh
./scripts/run_evals.sh
./scripts/run_all.sh --skip-perf   # skip the slow suite
```

```powershell
.\scripts\run_smoke.ps1
.\scripts\run_evals.ps1
.\scripts\run_all.ps1 -SkipPerf
```

The scripts use the same `Invoke-Compose` / `run_compose` helpers
as `test.ps1` / `test.sh` — never bare `docker compose` (false-passes
have happened in `test.ps1` history from this exact issue).

## Pytest markers

Defined in `testing/pytest.ini`:

| Marker | Requires | What |
|---|---|---|
| `smoke` | running stack | public endpoint shape |
| `regression` | running stack | per-bug reproductions |
| `integration` | `--run-integration` | multi-component |
| `e2e` | running stack + Playwright | browser flows |
| `perf` | running stack | latency / throughput |
| `security` | running stack or hermetic | auth / RBAC / injection |
| `a11y` | running stack + Playwright | axe-core |
| `eval` | running stack + LLM judge | LLM quality |

The default run (`pytest` from `testing/`) executes `smoke` only.

## LLM evaluation framework

`/testing/llm_evals/` is a hand-rolled eval framework with three layers:

1. **Deterministic scorers** (`exact_match`, `contains`, `regex`,
   `citation_count`, `tool_call_shape`, `refuses`, …) — fast, hermetic,
   always-on.
2. **Numeric metrics** (`precision_at_k`, `recall_at_k`, `mrr`, `ndcg`)
   — operate on the retrieval results.
3. **LLM-as-judge scorers** (`groundedness`, `faithfulness`,
   `answer_relevance`, `unsupported_claim_rate`,
   `missing_citation_rate`) — pluggable judge: Ollama (default),
   OpenAI (gold standard), or Heuristic (offline).

Scenarios live in `llm_evals/scenarios/test_*.py`; datasets live in
`llm_evals/datasets/*.jsonl`. The runner (`llm_evals/runners/run_eval.py`)
emits per-scenario records to `llm_evals/reports/<run-id>.jsonl` and
converts them to HTML / JSON / MD / CSV reports.

### Adding a new eval scenario

```python
# llm_evals/scenarios/test_my_eval.py
from llm_evals.eval import scenario, run, exact_match

@scenario(dataset="general_qa", scorers=[exact_match()], tags=["general_qa"])
async def test_capital_of_germany():
    await run(question="What is the capital of Germany?", expected="Berlin")
```

```json
# llm_evals/datasets/general_qa.jsonl (append a new line)
{"id": "qa-011", "category": "general_qa", "question": "What is the capital of Germany?", "expected_answer": "Berlin", "expected_citations": []}
```

Then:

```bash
cd testing && python -m pytest llm_evals/scenarios/test_my_eval.py -v
```

### Saving + comparing baselines

```bash
# Save the current run as the new baseline
python llm_evals/runners/run_eval.py --baseline

# Compare the next run to the saved baseline; fail on regression
python llm_evals/runners/run_eval.py --check
```

The regression detector uses two thresholds: a 0.10 absolute drop
per scenario, OR a 5% relative drop in the aggregate mean. Both must
be exceeded to fail.

## What's covered

### Backend unit tests (`backend/tests/`)

See [the top-level README](../README.md) and the in-tree
[`backend/tests/`](../../backend/tests) for the canonical list.

### Cross-cutting tests (`/testing/`)

| Suite | What |
|---|---|
| `workflows/smoke/` | 5 files, ~12 tests: `/health`, `/model`, `/metrics`, auth, documents, chat |
| `workflows/integration/` | 5 files, ~10 tests: RAG chat, tool calling, streaming, conversation memory, live connector |
| `workflows/security/` | 12 files, ~30 tests: SQL injection, XSS, prompt injection, CSRF, SSRF, upload validation, encryption, secrets, RBAC, auth bypass, rate limit, input validation |
| `workflows/performance/` | 4 files + locust: chat latency, embedding throughput, indexing speed, retrieval latency, concurrent users |
| `workflows/e2e/` | 5 files: onboarding, upload-then-query, connector UI, responsive design |
| `workflows/accessibility/` | 1 file: axe-core |
| `llm_evals/scenarios/` | 5 files: general_qa, refusal, prompt_injection, citations, tool_calling |
| `llm_evals/datasets/` | 8 JSONL files: general_qa, multi_turn, multi_doc, long_context, refusal, tool_calling, prompt_injection, citations |

## CI

See [ci.md](ci.md). The `ci.yml` workflow runs the unit, smoke,
security, eval (heuristic judge), coverage, and static suites on
every PR. The `nightly.yml` workflow runs the full perf + eval
suite at 03:17 UTC.

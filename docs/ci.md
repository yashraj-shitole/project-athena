# Continuous Integration

> Project Athena's first real CI. Lives under `.github/workflows/`; the
> framework and the per-suite runners live under [`/testing/`](../testing/README.md).
> This page documents the pipeline, the secrets, and how to add a new job.

## Workflows

| File | Triggers | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | every PR + push to `main` | unit, smoke, security, eval (Ollama), coverage, static |
| `.github/workflows/nightly.yml` | daily 03:17 UTC + manual | performance + locust + gold-standard eval (if `OPENAI_API_KEY` set) |
| `.github/workflows/pr-comment.yml` | on completion of `ci` | posts a coverage + eval summary as a PR comment |
| `.github/workflows/release.yml` | tag `v*` | full eval, save as new baseline, publish artifacts |

## Job matrix (ci.yml)

| Job | Service containers | What it runs |
|---|---|---|
| `unit` | — | `pytest backend/tests` (the existing hermetic suite) |
| `smoke` | postgres + redis + ollama | `pytest testing/workflows/smoke` + `workflows/security` |
| `eval` | postgres + redis + ollama | `pytest testing/llm_evals/scenarios -m "eval and not slow"` (heuristic judge; fast gate) |
| `coverage` | — | `coverage.py` on `app/`, posts to `pr-comment.yml` |
| `static` | — | `bandit`, `pip-audit` |
| `required` | — | fail the run if any required job failed |

## Secrets and vars

| Where | What | Default |
|---|---|---|
| `ci.yml` env | `ATHENA_JWT_SECRET` | `ci-test-secret-do-not-use-in-prod` |
| `ci.yml` env | `ATHENA_CONNECTOR_KEY` | `ci-fernet-test-key` |
| `nightly.yml` vars | `OPENAI_API_KEY` | unset (the gold-standard eval job skips itself) |
| `pr-comment.yml` secrets | `GITHUB_TOKEN` | automatic |

The CI env overrides are deliberately not production-grade. The
nightly job is the only place that needs a real `OPENAI_API_KEY`
for the gold-standard judge.

## Required status

`ci.yml` defines a `required` job that gates PR merging:

```yaml
required:
  if: always()
  steps:
    - run: |
        if [ "${{ contains(needs.*.result, 'failure') }}" = "true" ]; then
          exit 1
        fi
```

This means a PR cannot merge unless `unit`, `smoke`, and `coverage`
all pass. `eval` and `static` are advisory.

## Adding a new job

1. Add the job to `.github/workflows/ci.yml` (or create a new file
   under `.github/workflows/`; GitHub picks up new YAML files
   automatically).
2. If the job needs a service, declare it under `services:`.
3. If the job produces a `coverage.xml` or eval JSONL, add it to
   `pr-comment.yml`'s artifact list so the next run picks it up.
4. If the job's failure should block PRs, add it to the
   `required` job's `needs:` list.

## See also

- [`/testing/ci/README.md`](../testing/ci/README.md) — the framework-side
  view of the same workflows.
- [`/testing/README.md`](../testing/README.md) — the framework's
  entry point.
- [`docs/testing.md`](testing.md) — the backend unit + integration
  test reference.

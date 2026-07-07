# CI — GitHub Actions

This directory holds the project's GitHub Actions workflows and the helper
scripts they invoke. The workflows are the project's first real CI: every
PR runs a `unit + smoke + security + eval-on-Ollama + coverage` matrix.

## Layout

```
ci/
├── workflows/
│   ├── ci.yml         Main pipeline; runs on every PR + push to main.
│   ├── nightly.yml    Slow + heavy: full perf, full eval, dep audit.
│   ├── pr-comment.yml Posts an eval/coverage summary to the PR.
│   └── release.yml    Tag-triggered; full eval against a real provider.
└── scripts/
    ├── post_pr_summary.py
    └── publish_artifacts.py
```

## Job matrix (ci.yml)

| Job | Service container | What it runs |
|---|---|---|
| `unit` | — | `pytest backend/tests` (the existing hermetic suite) |
| `smoke` | api | `pytest testing/workflows/smoke` against a built `athena-api` |
| `security` | api | `pytest testing/workflows/security` + `bandit` + `pip-audit` |
| `eval` | api + ollama | `pytest testing/llm-evals/scenarios` (Ollama judge) |
| `coverage` | api | `coverage.py` on `app/`; posts to PR via `pr-comment.yml` |
| `frontend` | web-dev | `vitest run` |

The `nightly.yml` adds `pytest testing/workflows/performance` and a full
eval against the `openai` judge (only if `OPENAI_API_KEY` is set in secrets).

## Why GitHub Actions?

Lowest friction for an OSS-style repo: free public minutes, matrix builds,
artifact storage, reusable actions, and inline `secrets:` injection. The
choice is documented in the plan; swapping to a different provider later
means rewriting only the workflow files.

## Adding a new job

1. Add the step to `ci/workflows/ci.yml` (or create a new file in
   `ci/workflows/` — Actions picks it up automatically).
2. If it needs a service container, declare it under `services:`.
3. If it posts to the PR, hand off the output file to `pr-comment.yml`'s
   `workflow_run` trigger and have `post_pr_summary.py` format it.

## Secrets

The eval job needs no external secrets for the default Ollama judge. The
nightly job reads `OPENAI_API_KEY` from `vars.SECRETS_CONTEXT` and skips
itself if the key is absent.

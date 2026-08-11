#!/usr/bin/env bash
# Run the LLM evaluation suite. Mirrors run_evals.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../../scripts/goThrough/_helpers.sh"
enter_project_root
RUNNER="$ATHENA_REPO_ROOT/testing/llm_evals/runners/run_eval.py"

JUDGE="ollama"
BASELINE=""
CHECK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --judge) JUDGE="$2"; shift 2 ;;
    --baseline) BASELINE="--baseline"; shift ;;
    --check) CHECK="--check"; shift ;;
    *) log_err "Unknown arg: $1"; exit 2 ;;
  esac
done

log_step "Running eval suite (judge=$JUDGE)"
python "$RUNNER" --judge "$JUDGE" $BASELINE $CHECK

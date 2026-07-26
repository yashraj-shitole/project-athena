#!/usr/bin/env bash
# Run every testing/ suite. Mirrors run_all.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../../scripts/goThrough/_helpers.sh"
enter_project_root
check_prereqs || exit 1

echo "====================== Athena testing: all suites ======================"

ok=1
SKIP_PERF=""
SKIP_EVALS=""
[[ "${1:-}" == "--skip-perf" ]] && SKIP_PERF="--skip-perf"
[[ "${1:-}" == "--skip-evals" ]] && SKIP_EVALS="--skip-evals"

"$SCRIPT_DIR/run_smoke.sh"    || ok=0
"$SCRIPT_DIR/run_security.sh" || ok=0
[[ -z "$SKIP_EVALS" ]] && "$SCRIPT_DIR/run_evals.sh" || ok=$ok
[[ -z "$SKIP_PERF"  ]] && "$SCRIPT_DIR/run_perf.sh"  || ok=$ok

if [[ $ok -eq 1 ]]; then
  log_ok 'All testing/ suites passed'
  exit 0
else
  log_err 'One or more testing/ suites failed'
  exit 1
fi

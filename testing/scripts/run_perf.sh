#!/usr/bin/env bash
# Run the performance test suite. Mirrors run_perf.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../goThrough/_helpers.sh"
enter_project_root
log_step 'Performance benchmarks'
run_compose -- run --rm --no-deps api python -m pytest -ra --tb=short \
  -m 'perf and not integration' testing/workflows/performance
log_ok 'Performance benchmarks passed'

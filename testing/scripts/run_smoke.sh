#!/usr/bin/env bash
# Run the /testing/ smoke suite against a running api container.
# Mirrors run_smoke.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../../scripts/goThrough/_helpers.sh"
enter_project_root
check_prereqs || exit 1

log_step 'Smoke tests'
INTEGRATION="${1:-}"
MARKERS='smoke'
[[ "$INTEGRATION" == "--integration" ]] && MARKERS='smoke or integration'

run_compose -- run --rm --no-deps api python -m pytest -ra --tb=short -m "$MARKERS" \
  testing/workflows/smoke testing/workflows/integration
log_ok 'Smoke tests passed'

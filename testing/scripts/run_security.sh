#!/usr/bin/env bash
# Run the security test suite. Mirrors run_security.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../goThrough/_helpers.sh"
enter_project_root
log_step 'Security tests'
run_compose -- run --rm --no-deps api python -m pytest -ra --tb=short \
  -m security testing/workflows/security
log_ok 'Security tests passed'

#!/usr/bin/env bash
# Run the project test suites (macOS/Linux equivalent of test.ps1).
#
# Runs tests INSIDE the Docker containers (no local Python/Node venv needed).
#
# Usage:
#   ./test.sh                 run all (backend unit + frontend + e2E[skip])
#   ./test.sh --backend        backend unit tests only
#   ./test.sh --frontend       frontend tests only (skipped if none configured)
#   ./test.sh --integration     backend integration tests (starts infra)
#   ./test.sh --e2e             E2E (skipped  -  Phase 2)
#   ./test.sh --coverage        backend unit tests with pytest-cov
#   ./test.sh --ci              CI mode: unit + coverage, fail-fast
#   ./test.sh --verbose         verbose pytest
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/goThrough/_helpers.sh"
enter_project_root

BACKEND=0; FRONTEND=0; INTEGRATION=0; E2E=0; COVERAGE=0; CI=0; VERBOSE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend|-backend) BACKEND=1 ;;
    --frontend|-frontend) FRONTEND=1 ;;
    --integration|-integration) INTEGRATION=1 ;;
    --e2e|-e2e) E2E=1 ;;
    --coverage|-coverage) COVERAGE=1 ;;
    --ci|-ci) CI=1 ;;
    --verbose|-verbose) VERBOSE=1; export VERBOSE ;;
    -h|--help) sed -n '5,16p' "$0"; exit 0 ;;
    *) log_err "Unknown argument: $1"; exit 2 ;;
  esac
  shift || true
done

RESULTS_FILE="$(mktemp)"
add_result() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$RESULTS_FILE"; }

run_backend_unit() {
  log_step 'Backend unit tests (hermetic, in-memory SQLite)'
  local -a c=(run --rm --no-deps api python -m pytest)
  [[ $VERBOSE -eq 1 ]] && c+=(-v)
  [[ $CI -eq 1 ]] && c+=(--tb=short --maxfail=1 -q)
  if run_compose -- "${c[@]}"; then add_result BackendUnit PASS 'unit tests passed'; return 0
  else add_result BackendUnit FAIL 'pytest exited non-zero'; return 1; fi
}

run_backend_integration() {
  log_step 'Backend integration tests (starts postgres/redis/ollama)'
  local -a c=(run --rm api python -m pytest --run-integration)
  [[ $VERBOSE -eq 1 ]] && c+=(-v)
  if run_compose -- "${c[@]}"; then add_result BackendIntegration PASS 'integration tests passed'; return 0
  else add_result BackendIntegration FAIL 'pytest exited non-zero'; return 1; fi
}

run_coverage() {
  log_step 'Backend coverage (pytest-cov installed lazily)'
  local cmd='pip install --user --quiet pytest-cov 2>/dev/null; python -m pytest --cov=app --cov-report=term-missing'
  if run_compose -- run --rm --no-deps api sh -lc "$cmd"; then add_result Coverage PASS 'coverage report printed above'; return 0
  else add_result Coverage FAIL 'coverage run failed'; return 1; fi
}

frontend_has_tests() {
  [[ -f "$ATHENA_REPO_ROOT/frontend/package.json" ]] || return 1
  python3 - <<'PY' 2>/dev/null || node -e 'const p=require("./frontend/package.json");process.exit(p.scripts&&p.scripts.test?0:1)' 2>/dev/null
import json,sys
p=json.load(open("frontend/package.json"))
sys.exit(0 if p.get("scripts",{}).get("test") else 1)
PY
}

run_frontend() {
  if ! frontend_has_tests; then
    add_result Frontend SKIP 'no test script in frontend/package.json (Phase 2)'
    return 0
  fi
  log_step 'Frontend tests'
  if run_compose -- run --rm --no-deps web-dev npm test; then add_result Frontend PASS 'frontend tests passed'; return 0
  else add_result Frontend FAIL 'npm test exited non-zero'; return 1; fi
}

run_e2e() { add_result E2E SKIP 'Playwright E2E not configured (Phase 2)'; return 0; }

if ! check_prereqs; then exit 1; fi

ANY=$((BACKEND+FRONTEND+INTEGRATION+E2E+COVERAGE))
RUN_ALL=0; [[ $ANY -eq 0 ]] && RUN_ALL=1
[[ $CI -eq 1 ]] && { COVERAGE=1; }
OK=1

need_api=$((RUN_ALL+BACKEND+INTEGRATION+COVERAGE+CI)); [[ $need_api -gt 0 ]] && {
  [[ -z "$(docker images --filter reference=athena-api -q 2>/dev/null)" ]] && { log_step 'Building api image (missing)'; run_compose build api || exit 1; }
}
need_web=$((RUN_ALL+FRONTEND)); [[ $need_web -gt 0 ]] && {
  [[ -z "$(docker images --filter reference=athena-web-dev -q 2>/dev/null)" ]] && { log_step 'Building web-dev image (missing)'; run_compose build web-dev || exit 1; }
}

if [[ $CI -eq 1 ]]; then
  run_backend_unit || OK=0
  run_coverage || OK=0
elif [[ $RUN_ALL -eq 1 ]]; then
  run_backend_unit || OK=0
  run_frontend || OK=0
  run_e2e || OK=0
else
  [[ $COVERAGE -eq 1 ]] && { run_coverage || OK=0; }
  [[ $BACKEND -eq 1 ]] && { run_backend_unit || OK=0; }
  [[ $INTEGRATION -eq 1 ]] && { run_backend_integration || OK=0; }
  [[ $FRONTEND -eq 1 ]] && { run_frontend || OK=0; }
  [[ $E2E -eq 1 ]] && { run_e2e || OK=0; }
fi

echo '====================== Test summary ======================'
printf '%-20s %-8s %s\n' 'Suite' 'Status' 'Detail'
while IFS=$'\t' read -r name status detail; do
  printf '%-20s %-8s %s\n' "$name" "$status" "$detail"
done < "$RESULTS_FILE"
echo '========================================================='
fails=$(awk -F'\t' '$2=="FAIL"' "$RESULTS_FILE" | wc -l)
rm -f "$RESULTS_FILE"
if [[ $fails -gt 0 ]]; then log_err "$fails suite(s) failed."; exit 1; fi
log_ok 'All requested suites passed (or were skipped).'
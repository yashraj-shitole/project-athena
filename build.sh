#!/usr/bin/env bash
# Root build script (macOS/Linux equivalent of build.ps1).
#
# Builds all Athena Docker images in dependency order (api -> nginx [, web-dev]),
# after validating prerequisites/env and ensuring networks/volumes exist.
#
# Usage:
#   ./build.sh                      build all
#   ./build.sh --clean               remove existing athena-* images first
#   ./build.sh --no-cache            ignore the layer cache
#   ./build.sh --service backend    build one service (backend|frontend|web-dev|db|redis|ollama|ai-service|worker)
#   ./build.sh --include-dev        also build the web-dev (Vite) image
#   ./build.sh --production          enforce prod-grade env validation
#   ./build.sh --verbose             --progress=plain
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/goThrough/_helpers.sh"
enter_project_root

CLEAN=0; NO_CACHE=0; INCLUDE_DEV=0; PRODUCTION=0; SERVICE=""; VERBOSE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean|-clean) CLEAN=1 ;;
    --no-cache|-nocache) NO_CACHE=1 ;;
    --include-dev|-includedev) INCLUDE_DEV=1 ;;
    --production|-production) PRODUCTION=1 ;;
    --verbose|-verbose) VERBOSE=1; export VERBOSE ;;
    --service|-service) shift; [[ $# -gt 0 ]] || { log_err "--service requires an argument"; exit 2; }; SERVICE="$1" ;;
    -h|--help)
      sed -n '3,14p' "$0"; exit 0 ;;
    *) log_err "Unknown argument: $1"; exit 2 ;;
  esac
  shift || true
done

build_images() {
  local -a svcs=("$@")
  local -a cmd=(build)
  [[ $NO_CACHE -eq 1 ]] && cmd+=(--no-cache)
  [[ $VERBOSE -eq 1 ]] && cmd+=(--progress=plain)
  for s in "${svcs[@]}"; do log_step "Building image: $s"; done
  run_compose "${cmd[@]}" "${svcs[@]}"
}

if ! check_prereqs; then exit 1; fi
if ! check_required_env $([[ $PRODUCTION -eq 1 ]] && echo strict); then exit 1; fi
ensure_networks_and_volumes

log_step 'Pulling infra images (postgres, redis, ollama, ollama-pull)'
run_compose pull --ignore-pull-failures postgres redis ollama ollama-pull || true

if [[ $CLEAN -eq 1 ]]; then
  log_step 'Clean: removing existing athena-* images'
  for img in athena-api athena-nginx athena-web-dev; do docker image rm -f "$img" 2>/dev/null || true; done
fi

if [[ -n "$SERVICE" ]]; then
  svc="$(resolve_service "$SERVICE")"
  log_info "Target service: $svc"
  case "$svc" in
    api) build_images api ;;
    nginx) build_images nginx; [[ $INCLUDE_DEV -eq 1 ]] && build_images web-dev ;;
    web-dev) build_images web-dev ;;
    postgres|redis|ollama|ollama-pull)
      log_info "$svc uses a prebuilt image; pulling."
      run_compose pull "$svc" ;;
    *) log_err "Unknown service '$SERVICE' (resolved '$svc')."; exit 1 ;;
  esac
  log_ok "Build complete: $svc"
  exit 0
fi

build_images api
build_images nginx
[[ $INCLUDE_DEV -eq 1 ]] && build_images web-dev
log_ok 'All images built.'
log_info 'Next: ./docker-up.sh            (run the stack)'
log_info '     ./docker-up.sh --debug     (hot reload + debugger)'
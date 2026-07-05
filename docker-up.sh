#!/usr/bin/env bash
# Root entry point for starting the Athena stack (macOS/Linux, wraps _helpers.sh).
#
# Usage:
#   ./docker-up.sh                       normal mode
#   ./docker-up.sh --debug               debug mode (hot reload + debugger)
#   ./docker-up.sh --debug --watch        watch mode (follow logs)
#   ./docker-up.sh --services backend,frontend
#   ./docker-up.sh --build               rebuild before starting
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/goThrough/_helpers.sh"
enter_project_root

DEBUG=0; WATCH=0; SERVICES=""; BUILD=0; NO_CACHE=0; DETACHED=1; TIMEOUT=120; VERBOSE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug|-debug) DEBUG=1 ;;
    --watch|-watch) WATCH=1 ;;
    --services|-services) shift; [[ $# -gt 0 ]] || { log_err "--services requires an argument"; exit 2; }; SERVICES="$1" ;;
    --build|-build) BUILD=1 ;;
    --no-cache|-nocache) NO_CACHE=1 ;;
    --foreground|-foreground) DETACHED=0 ;;
    --timeout|-timeout) shift; [[ $# -gt 0 ]] || { log_err "--timeout requires an argument"; exit 2; }; TIMEOUT="$1" ;;
    --verbose|-verbose) VERBOSE=1; export VERBOSE ;;
    -h|--help) sed -n '3,12p' "$0"; exit 0 ;;
    *) log_err "Unknown argument: $1"; exit 2 ;;
  esac
  shift || true
done
[[ $WATCH -eq 1 ]] && DEBUG=1

if [[ -n "$SERVICES" ]]; then
  IFS=',' read -ra svc_arr <<< "$SERVICES"
  svc_list=()
  for s in "${svc_arr[@]}"; do svc_list+=("$(resolve_service "$s")"); done
else
  if [[ $DEBUG -eq 1 ]]; then
    svc_list=(postgres redis ollama ollama-pull api web-dev)
  else
    svc_list=(postgres redis ollama ollama-pull api nginx)
  fi
fi
# health set excludes the one-shot ollama-pull
health_list=()
for s in "${svc_list[@]}"; do [[ "$s" != "ollama-pull" ]] && health_list+=("$s"); done

if ! check_prereqs; then exit 1; fi
if ! check_required_env; then exit 1; fi
ensure_networks_and_volumes

if [[ $BUILD -eq 1 ]]; then
  log_step 'Building images before start'
  cmd=(build); [[ $NO_CACHE -eq 1 ]] && cmd+=(--no-cache); [[ $VERBOSE -eq 1 ]] && cmd+=(--progress=plain)
  if ! run_compose $([[ $DEBUG -eq 1 ]] && echo --debug) -- "${cmd[@]}" "${svc_list[@]}"; then
    log_err 'Build failed; aborting startup.'; exit 1
  fi
else
  need_build=0
  for s in "${svc_list[@]}"; do
    case "$s" in
      api|nginx|web-dev)
        if [[ -z "$(docker images --filter reference=athena-$s -q 2>/dev/null)" ]]; then need_build=1; break; fi ;;
    esac
  done
  if [[ $need_build -eq 1 ]]; then
    log_step 'One or more images are missing  -  building (use --build to force)'
    if ! run_compose $([[ $DEBUG -eq 1 ]] && echo --debug) -- build "${svc_list[@]}"; then
      log_err 'Build failed; aborting startup.'; exit 1
    fi
  fi
fi

mode=""
[[ $DEBUG -eq 1 ]] && mode+=" (debug)"
[[ $WATCH -eq 1 ]] && mode+=" (watch)"
log_step "Starting: ${svc_list[*]}$mode"
up_cmd=(up); [[ $BUILD -eq 1 ]] && up_cmd+=(--build); [[ $DETACHED -eq 1 ]] && up_cmd+=(-d)
if ! run_compose $([[ $DEBUG -eq 1 ]] && echo --debug) -- "${up_cmd[@]}" "${svc_list[@]}"; then
  log_err 'docker compose up failed. Dumping recent logs:'
  run_compose $([[ $DEBUG -eq 1 ]] && echo --debug) -- logs --tail 80 "${svc_list[@]}" || true
  exit 1
fi

log_step "Waiting for health (timeout ${TIMEOUT}s)"
export ATHENA_HEALTH_TIMEOUT="$TIMEOUT"
if ! wait_healthy "${health_list[@]}"; then
  log_err 'One or more services failed to become healthy. Dumping logs:'
  run_compose $([[ $DEBUG -eq 1 ]] && echo --debug) -- logs --tail 120 "${health_list[@]}" || true
  exit 1
fi

show_service_urls
show_compose_status $([[ $DEBUG -eq 1 ]] && echo --debug)
log_ok 'Stack is up.'

if [[ $WATCH -eq 1 ]]; then
  log_step 'Watch mode: following logs (Ctrl-C to detach  -  containers keep running)'
  log_info 'Backend edits reload via uvicorn --reload; frontend via Vite HMR.'
  run_compose $([[ $DEBUG -eq 1 ]] && echo --debug) -- logs -f --tail 100 "${health_list[@]}" || true
  log_info 'Containers are still running. Stop with: ./goThrough/docker-down.sh'
fi
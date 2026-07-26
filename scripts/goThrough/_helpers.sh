#!/usr/bin/env bash
# Shared helper functions for Project Athena developer scripts (POSIX-ish
# bash). Sourced by build.sh / docker-up.sh / test.sh at the repo root.
#
# Mirrors goThrough/_helpers.ps1:
#   - colored output
#   - docker compose invocation (COMPOSE_PROJECT_NAME=athena, file order,
#     --profile dev when debug)
#   - service <-> container name map
#   - prerequisite + environment validation
#   - network/volume bootstrapping
#   - health-check polling
#
# Service map (matches infra/docker-compose.yml):
#   backend  -> service api   (container athena-api)
#   frontend -> service nginx (container athena-nginx) or web-dev (HMR)
#   ai-service / worker -> folded into api
set -euo pipefail

# Requires bash 4+ (associative-array-free, but we use ${arr[@]} expansions
# that error on empty arrays under `set -u` in bash 3.2). macOS ships bash 3.2
# by default  -  install a modern bash: `brew install bash`, then ensure
# /opt/homebrew/bin (or /usr/local/bin) is ahead of /usr/bin on PATH.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  printf 'athena: bash 4+ is required (you have %s). On macOS: brew install bash.\n' "${BASH_VERSION:-unknown}" >&2
  exit 1
fi

ATHENA_PROJECT_NAME="athena"
ATHENA_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ATHENA_COMPOSE_DIR="$ATHENA_REPO_ROOT/infra"
ATHENA_COMPOSE_FILE="$ATHENA_COMPOSE_DIR/docker-compose.yml"
ATHENA_DEBUG_FILE="$ATHENA_COMPOSE_DIR/docker-compose.debug.yml"

# service -> container name. Kept as a function (not an associative array) so
# this file also works on macOS default bash 3.2, which lacks `declare -A`.
container_for() {
  case "$1" in
    postgres) echo athena-postgres ;;
    redis) echo athena-redis ;;
    ollama) echo athena-ollama ;;
    ollama-pull) echo athena-ollama-pull ;;
    api) echo athena-api ;;
    nginx) echo athena-nginx ;;
    web-dev) echo athena-web-dev ;;
    *) echo "" ;;
  esac
}

# ---------------------------------------------------------------------------
# Color output (honor NO_COLOR / non-tty)
# ---------------------------------------------------------------------------
if [[ -t 1 && "${NO_COLOR:-}" == "" ]]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'; C_GRAY=$'\033[90m'; C_RESET=$'\033[0m'
else
  C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_GRAY=""; C_RESET=""
fi

log_step() { printf '%s=> %s%s\n' "$C_CYAN" "$*" "$C_RESET"; }
log_ok()   { printf '%s[OK]   %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
log_warn() { printf '%s[WARN] %s%s\n' "$C_YELLOW" "$*" "$C_RESET" >&2; }
log_err()  { printf '%s[ERR]  %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; }
log_info() { printf '%s       %s%s\n' "$C_GRAY" "$*" "$C_RESET"; }

enter_project_root() { cd "$ATHENA_REPO_ROOT"; }

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
check_prereqs() {
  if ! command -v docker >/dev/null 2>&1; then
    log_err "Docker is not installed or not on PATH."
    return 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    log_err "The 'docker compose' plugin is not available."
    return 1
  fi
  if ! docker info >/dev/null 2>&1; then
    log_err "The Docker daemon is not running. Start it and retry."
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Env validation
# ---------------------------------------------------------------------------
check_required_env() {
  # $1 = "strict" to enforce prod-grade rules
  local strict="${1:-}"
  local env="${ATHENA_ENV:-dev}"
  local is_dev=0
  case "$(echo "$env" | tr '[:upper:]' '[:lower:]')" in
    dev|development|test|local) is_dev=1 ;;
  esac
  local jwt="${ATHENA_JWT_SECRET:-}"
  if [[ "$strict" == "strict" ]] || [[ "$is_dev" -eq 0 ]]; then
    case "$jwt" in
      ""|change-me-in-prod|secret|changeme)
        log_err "ATHENA_JWT_SECRET is unset or a known placeholder. Refusing to run outside dev."
        log_err "Set a strong secret, e.g.: export ATHENA_JWT_SECRET=\$(openssl rand -hex 32)"
        return 1 ;;
    esac
    if [[ "$(echo "$env" | tr '[:upper:]' '[:lower:]')" == "prod" ]]; then
      if echo "${ATHENA_CORS_ORIGINS:-}" | grep -q 'http://localhost'; then
        log_err "ATHENA_CORS_ORIGINS contains a localhost origin in prod."
        return 1
      fi
    fi
  fi
  case "$jwt" in
    change-me-in-prod) log_warn "ATHENA_JWT_SECRET is the shipped placeholder  -  fine for dev, never for prod." ;;
  esac
  return 0
}

# ---------------------------------------------------------------------------
# Compose invocation
# ---------------------------------------------------------------------------
# build_compose_args [--debug] -> echoes the prefix args
build_compose_args() {
  local debug=0
  [[ "${1:-}" == "--debug" ]] && debug=1
  local args=()
  if [[ "$debug" -eq 1 ]]; then args+=(--profile dev); fi
  args+=(-f "$ATHENA_COMPOSE_FILE")
  if [[ "$debug" -eq 1 ]]; then
    if [[ -f "$ATHENA_DEBUG_FILE" ]]; then
      args+=(-f "$ATHENA_DEBUG_FILE")
    else
      log_warn "Debug compose file not found at $ATHENA_DEBUG_FILE; using base only."
    fi
  fi
  printf '%s\0' "${args[@]}"
}

# run_compose [--debug] -- <compose subcommand + args...>
# Sets COMPOSE_PROJECT_NAME and execs docker compose, streaming output.
run_compose() {
  local debug=0
  if [[ "${1:-}" == "--debug" ]]; then debug=1; shift; fi
  [[ "${1:-}" == "--" ]] && shift
  local -a prefix=()
  while IFS= read -r -d '' a; do prefix+=("$a"); done < <(build_compose_args $([[ $debug -eq 1 ]] && echo --debug))
  if [[ -n "${VERBOSE:-}" ]]; then printf '%s+ docker compose %s%s\n' "$C_GRAY" "${prefix[*]} $*" "$C_RESET" >&2; fi
  COMPOSE_PROJECT_NAME="$ATHENA_PROJECT_NAME" docker compose "${prefix[@]}" "$@"
  return $?
}

# ---------------------------------------------------------------------------
# Network + volume bootstrap (best effort, idempotent)
# ---------------------------------------------------------------------------
ensure_networks_and_volumes() {
  local net="$ATHENA_PROJECT_NAME"_default
  log_step "Ensuring Docker network '$net' and named volumes exist"
  docker network inspect "$net" >/dev/null 2>&1 || docker network create "$net" >/dev/null 2>&1 || true
  local v
  for v in pgdata ollama_data api_storage webdev_node_modules; do
    local name="$ATHENA_PROJECT_NAME"_"$v"
    if ! docker volume inspect "$name" >/dev/null 2>&1; then
      docker volume create "$name" >/dev/null 2>&1 || true
      log_info "created volume $name"
    fi
  done
}

# ---------------------------------------------------------------------------
# Health polling
# ---------------------------------------------------------------------------
container_health() {
  # $1 = container name -> echoes healthy|unhealthy|starting|missing
  local c="$1"
  if ! docker inspect --format '{{.Id}}' "$c" >/dev/null 2>&1; then echo "missing"; return; fi
  local has_hc; has_hc="$(docker inspect --format '{{if .Config.Healthcheck}}yes{{else}}no{{end}}' "$c" 2>/dev/null || echo no)"
  if [[ "$has_hc" == "yes" ]]; then
    local h; h="$(docker inspect --format '{{.State.Health.Status}}' "$c" 2>/dev/null || true)"
    [[ -z "$h" ]] && h="starting"
    echo "$h"
  else
    local st; st="$(docker inspect --format '{{.State.Status}}' "$c" 2>/dev/null || true)"
    if [[ "$st" == "running" ]]; then echo "healthy"; else echo "starting"; fi
  fi
}

wait_healthy() {
  # $@ = services. Returns 0 if all healthy within timeout.
  # Uses a space-separated pending string (not an array) so the empty-array
  # expansion under `set -u` on bash 4.0-4.3 cannot abort us mid-loop. Service
  # names contain no spaces, so this is safe.
  local timeout="${ATHENA_HEALTH_TIMEOUT:-120}"
  local interval=3
  local deadline=$(( $(date +%s) + timeout ))
  local pending="$*"
  local first=1
  while [[ -n "$pending" && $(date +%s) -lt $deadline ]]; do
    local next=""
    local svc
    for svc in $pending; do
      local c; c="$(container_for "$svc")"
      if [[ -z "$c" ]]; then log_warn "Unknown service '$svc'; skipping"; continue; fi
      local h; h="$(container_health "$c")"
      case "$h" in
        healthy) log_ok "$svc ($c) is healthy" ;;
        missing) log_warn "$svc ($c) container not found  -  did it start?" ;;
        *) next="$next $svc"; if [[ $first -eq 1 ]]; then log_info "waiting for $svc ($c): $h"; fi ;;
      esac
    done
    first=0
    pending="${next# }"   # strip the leading space we accumulated
    [[ -n "$pending" ]] && sleep "$interval"
  done
  if [[ -n "$pending" ]]; then
    for svc in $pending; do log_err "$svc did not become healthy within ${timeout}s"; done
    return 1
  fi
  return 0
}

show_service_urls() {
  echo
  echo "Service URLs:"
  printf '  %-10s %s\n' "api"      "http://localhost:8000  (direct API; loopback only)"
  printf '  %-10s %s\n' "nginx"    "http://localhost:8080  (SPA + reverse proxy)"
  printf '  %-10s %s\n' "web-dev"  "http://localhost:5173  (Vite HMR)"
  printf '  %-10s %s\n' "postgres" "localhost:5432          (loopback only)"
  printf '  %-10s %s\n' "redis"    "localhost:6379          (loopback only)"
  printf '  %-10s %s\n' "ollama"   "http://ollama:11434     (in-network only; not published)"
  echo
}

show_compose_status() {
  local debug=0; [[ "${1:-}" == "--debug" ]] && debug=1
  echo
  echo "Container status:"
  local -a prefix=()
  while IFS= read -r -d '' a; do prefix+=("$a"); done < <(build_compose_args $([[ $debug -eq 1 ]] && echo --debug))
  COMPOSE_PROJECT_NAME="$ATHENA_PROJECT_NAME" docker compose "${prefix[@]}" ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}\t{{.Ports}}' || true
  echo
}

# Resolve a friendly alias to a compose service name.
resolve_service() {
  case "$(echo "$1" | tr '[:upper:]' '[:lower:]')" in
    backend|api) echo api ;;
    frontend|web|nginx) echo nginx ;;
    web-dev|vite|dev) echo web-dev ;;
    db|postgres|database) echo postgres ;;
    cache|redis) echo redis ;;
    llm|ollama) echo ollama ;;
    ai-service|ai|worker) echo api ;;
    *) echo "$1" ;;
  esac
}
#!/usr/bin/env bash
# End-to-end local test for LeadPulse MCP.
#
# This script does NOT manage MongoDB. Mongo is assumed to be running
# independently (matches prod: separate DB server, many MCP containers).
# You can point the MCP at ANY reachable Mongo:
#   * A local Mongo container you run yourself
#   * A native Mongo install on your machine
#   * Mongo Atlas (paste the full URI into MONGODB_URL)
#
# Usage:
#   bash scripts/test-local.sh                              # local dev CRM (http://localhost:3000)
#   bash scripts/test-local.sh run https://leadpulse.projexlight.com
#   bash scripts/test-local.sh https://leadpulse.projexlight.com  # shorthand
#   bash scripts/test-local.sh rebuild                      # --no-cache image rebuild
#   bash scripts/test-local.sh stop                         # remove MCP container
#   bash scripts/test-local.sh status
#   bash scripts/test-local.sh counts                       # Mongo collection counts
#   bash scripts/test-local.sh logs                         # docker logs -f on MCP
#
# Env overrides:
#   MONGODB_URL              # full Mongo URI the MCP container will use.
#                            # Default tries host.docker.internal:27017 with admin/password.
#                            # If you use Atlas, set MONGODB_URL to the SRV URI.
#   BUILD_MODE=prod          # use ./Dockerfile (Nuitka) instead of Dockerfile.dev
#   MCP_HOST_PORT=8001       # host port for the MCP container (default 8000)
#   IMAGE_TAG=dev            # image tag to build/run
#   LEADPULSE_TOKEN          # bearer token the CRM validates
#   INSTANCE_ID              # this container's identity reported to CRM

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRM_ROOT="${CRM_ROOT:-C:/Users/srima/projex_crm}"

# ---- Tunables --------------------------------------------------------------
IMAGE_NAME="${IMAGE_NAME:-projex-leadpulse-mcp}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
BUILD_MODE="${BUILD_MODE:-fast}"   # fast | prod

CONTAINER_NAME="${CONTAINER_NAME:-projex_leadpulse_mcp_dev}"
MCP_HOST_PORT="${MCP_HOST_PORT:-8000}"
MCP_URL="http://127.0.0.1:${MCP_HOST_PORT}"
BOOTSTRAP_SECRET="${BOOTSTRAP_SECRET:-dev-secret}"

# Mongo — ONLY a URL. We connect to it, we don't manage it.
# Default assumes Mongo is reachable from the MCP container via
# host.docker.internal:27017 (typical when Mongo runs natively or in another
# container with 27017 published on the host).
MONGODB_URL="${MONGODB_URL:-mongodb://admin:password@host.docker.internal:27017/?authSource=admin}"
MONGODB_DB="${MONGODB_DB:-leadpulse_mcp_db}"

LEADPULSE_TOKEN="${LEADPULSE_TOKEN:-dev-token-at-least-16-chars}"
INSTANCE_ID="${INSTANCE_ID:-local-$(hostname)-$$}"
SENDER_AGENTS="${SENDER_AGENTS:-2}"

DEFAULT_CRM_URL="${LEADPULSE_URL:-http://localhost:3000}"

# ---- Pretty ----------------------------------------------------------------
if [[ -t 1 ]]; then
  C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_END="\033[0m"
else
  C_OK=""; C_WARN=""; C_ERR=""; C_END=""
fi
say()  { printf "%b==> %s%b\n" "${C_OK}" "$*" "${C_END}"; }
warn() { printf "%b!! %s%b\n"  "${C_WARN}" "$*" "${C_END}"; }
err()  { printf "%b!! %s%b\n"  "${C_ERR}" "$*" "${C_END}"; }

# ---- Helpers ---------------------------------------------------------------
http_code() { curl -s -o /dev/null -w "%{http_code}" "$@" || echo "000"; }

wait_http() {
  local url="$1"; local want="${2:-200}"; local deadline=$(( $(date +%s) + 60 ))
  while [[ $(date +%s) -lt $deadline ]]; do
    [[ "$(http_code "$url")" == "$want" ]] && return 0
    sleep 1
  done
  return 1
}

# localhost → host.docker.internal for container-side URLs.
rewrite_for_container() {
  local raw="$1"
  printf '%s' "$raw" | sed -E 's#(://)(localhost|127\.0\.0\.1)(\b)#\1host.docker.internal\3#g'
}

container_running() { [[ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null || true)" == "true" ]]; }
container_exists()  { docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; }

read_crm_key() {
  local env_file="${CRM_ROOT}/server/.env"
  [[ -f "$env_file" ]] || { err "CRM .env not found at $env_file"; return 1; }
  local key
  key="$(grep -E '^MCP_BOOTSTRAP_KEY=' "$env_file" | head -n 1 | cut -d= -f2- | tr -d '\r' | tr -d '"' || true)"
  [[ -n "${key:-}" ]] || { err "MCP_BOOTSTRAP_KEY empty/missing in $env_file"; return 1; }
  printf '%s' "$key"
}

# Ping a Mongo URI using a throwaway mongosh container. Use --network host so
# host.docker.internal works the same way the MCP container will see it, and
# because this check runs from inside this helper container.
mongo_ping() {
  local uri="$1"
  # Swap host.docker.internal -> localhost for the ping container running
  # with --network host (which has no "host.docker.internal" DNS mapping).
  local ping_uri="$uri"
  if command -v uname >/dev/null && [[ "$(uname -s)" != MINGW* && "$(uname -s)" != CYGWIN* && "$(uname -s)" != MSYS* ]]; then
    ping_uri="$(printf '%s' "$uri" | sed 's|host\.docker\.internal|localhost|g')"
  else
    ping_uri="$(printf '%s' "$uri" | sed 's|host\.docker\.internal|localhost|g')"
  fi
  docker run --rm --network host mongo:7 \
    mongosh --quiet --eval 'db.runCommand({ping:1}).ok' "$ping_uri" 2>/dev/null \
    | tr -d '[:space:]' | grep -q '^1$'
}

# ---- Steps -----------------------------------------------------------------

step_preflight() {
  say "[0/5] Preflight checks"
  command -v docker >/dev/null || { err "docker not installed"; exit 1; }
  command -v curl   >/dev/null || { err "curl not installed"; exit 1; }

  local crm_host_url="$1"
  local code; code="$(http_code -X POST "${crm_host_url}/api/mcp/register")"
  [[ "$code" != "000" ]] || { err "CRM not responding at ${crm_host_url}."; exit 1; }
  printf "    CRM OK at %s (HTTP %s for POST /api/mcp/register — 400/401 expected)\n" "$crm_host_url" "$code"

  local key; key="$(read_crm_key)" || exit 1
  printf "    MCP_BOOTSTRAP_KEY found (%s…%s)\n" "${key:0:4}" "${key: -4}"
}

step_check_mongo() {
  say "[1/5] Probe Mongo (independent service)"
  printf "    URI: %s\n" "${MONGODB_URL}"
  if mongo_ping "$MONGODB_URL"; then
    printf "    ping OK\n"
  else
    err "Cannot reach Mongo at the configured URL."
    cat <<EOF
    The MCP does NOT manage Mongo. Start one yourself, then re-run.

    Quick options:
      (a) Use the convenience compose (creates a detached Mongo server):
            docker-compose -f docker-compose.shared-mongo.yml up -d
      (b) Point at an existing Mongo (Atlas, native install, etc.):
            MONGODB_URL='mongodb://user:pass@HOST:27017/?authSource=admin' \\
              bash scripts/test-local.sh
      (c) Mongo Atlas SRV:
            MONGODB_URL='mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority' \\
              bash scripts/test-local.sh

    Reason we couldn't reach it from here:
      - port not listening?   try:  nc -z localhost 27017
      - wrong credentials?    try:  mongosh "${MONGODB_URL/host.docker.internal/localhost}" --eval 'db.runCommand({ping:1}).ok'
EOF
    exit 1
  fi
}

step_build_image() {
  say "[2/5] Build MCP image (${FULL_IMAGE}, mode=${BUILD_MODE})"
  cd "$ROOT"
  local dockerfile
  case "$BUILD_MODE" in
    fast) dockerfile="Dockerfile.dev" ;;
    prod) dockerfile="Dockerfile" ;;
    *) err "Unknown BUILD_MODE=$BUILD_MODE (use fast|prod)"; exit 1 ;;
  esac
  [[ -f "$dockerfile" ]] || { err "$dockerfile not found at repo root"; exit 1; }
  local cache_flag=""
  [[ "${NO_CACHE:-0}" == "1" ]] && cache_flag="--no-cache"
  docker build $cache_flag -f "$dockerfile" -t "${FULL_IMAGE}" .
  printf "    image built: %s\n" "${FULL_IMAGE}"
}

step_run_container() {
  say "[3/5] Run MCP container (${CONTAINER_NAME} on :${MCP_HOST_PORT})"
  if container_running; then
    printf "    already running\n"
  else
    [[ $(container_exists; echo $?) -eq 0 ]] && docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

    # Preflight: make sure the host port is free. Checking from inside a short-
    # lived container with --network host catches both host-native processes
    # and Docker-published ports in one go.
    if (exec 3<>"/dev/tcp/127.0.0.1/${MCP_HOST_PORT}") 2>/dev/null; then
      exec 3<&-; exec 3>&-
      err "Host port ${MCP_HOST_PORT} is already in use."
      cat <<EOF
    Something else is bound to :${MCP_HOST_PORT}. Either:
      (a) free it   -> netstat -ano | grep :${MCP_HOST_PORT} ; taskkill //F //PID <pid>
      (b) stop a previous MCP run  -> bash scripts/test-local.sh stop
      (c) pick another port        -> MCP_HOST_PORT=8001 bash scripts/test-local.sh
EOF
      exit 1
    fi

    docker run -d \
      --name "${CONTAINER_NAME}" \
      --add-host host.docker.internal:host-gateway \
      -p "${MCP_HOST_PORT}:8000" \
      -e BOOTSTRAP_SECRET="${BOOTSTRAP_SECRET}" \
      -e MEM_LIMIT_MB="${MEM_LIMIT_MB:-1024}" \
      -e LOG_LEVEL="${LOG_LEVEL:-info}" \
      "${FULL_IMAGE}" >/dev/null
    printf "    started. logs: docker logs -f %s\n" "${CONTAINER_NAME}"
  fi

  printf "    waiting for /health on host:%s ..." "${MCP_HOST_PORT}"
  if ! wait_http "${MCP_URL}/health" 200; then
    printf " FAIL\n"; warn "Container did not become healthy. Last 30 log lines:"
    docker logs --tail 30 "${CONTAINER_NAME}" || true
    exit 1
  fi
  printf " OK\n"
  printf "    /health -> %s\n" "$(curl -s "${MCP_URL}/health")"
}

step_bootstrap() {
  say "[4/5] Bootstrap MCP (inject runtime config)"
  local crm_host_url="$1"
  local crm_container_url; crm_container_url="$(rewrite_for_container "$crm_host_url")"
  local mongodb_container_url; mongodb_container_url="$(rewrite_for_container "$MONGODB_URL")"

  if [[ "$crm_host_url" != "$crm_container_url" ]]; then
    printf "    CRM (host)     : %s\n" "$crm_host_url"
    printf "    CRM (container): %s\n" "$crm_container_url"
  else
    printf "    CRM            : %s\n" "$crm_container_url"
  fi
  printf "    Mongo          : %s\n" "$mongodb_container_url"

  local key; key="$(read_crm_key)" || exit 1
  local payload
  payload=$(cat <<EOF
{
  "mongodb_url": "${mongodb_container_url}",
  "mongodb_db": "${MONGODB_DB}",
  "leadpulse_url": "${crm_container_url}",
  "leadpulse_token": "${LEADPULSE_TOKEN}",
  "instance_id": "${INSTANCE_ID}",
  "mcp_bootstrap_key": "${key}",
  "sender_agents_per_container": ${SENDER_AGENTS}
}
EOF
)
  local resp
  resp="$(curl -sS -X POST "${MCP_URL}/api/v1/bootstrap" \
    -H "Content-Type: application/json" \
    -H "X-Bootstrap-Secret: ${BOOTSTRAP_SECRET}" \
    -d "$payload")"
  if echo "$resp" | grep -q '"success":true'; then
    printf "    bootstrap OK -> %s\n" "$resp"
  else
    err "Bootstrap failed: $resp"
    exit 1
  fi
}

step_verify() {
  say "[5/5] Verify MCP came alive"
  sleep 3
  printf "    /health            -> %s\n" "$(curl -s "${MCP_URL}/health")"
  printf "    /bootstrap/status  -> %s\n" "$(curl -s "${MCP_URL}/api/v1/bootstrap/status")"
  printf "\n    Recent container log events:\n"
  docker logs --tail 80 "${CONTAINER_NAME}" 2>&1 \
    | grep -E '"msg":"(bootstrap_received|mongo_connected|mcp_registered|supervisor_started|mcp_register_failed|heartbeat_post_failed|crm_circuit_open)"' \
    | tail -n 10 | sed 's/^/      /' || true
}

step_counts() {
  say "Mongo counts (reachable from mongosh-host)"
  local ping_uri; ping_uri="$(printf '%s' "$MONGODB_URL" | sed 's|host\.docker\.internal|localhost|g')"
  docker run --rm --network host mongo:7 mongosh --quiet "$ping_uri" \
    --eval "db = db.getSiblingDB('${MONGODB_DB}');
            ['campaign_contacts','refined_contacts','send_queue','campaign_leases','mcp_instance_registry','campaign_stats','ingest_errors','pending_crm_events','audit_log']
              .forEach(c => print('  ' + c.padEnd(20) + ': ' + db.getCollection(c).countDocuments()));"
}

step_summary() {
  printf "\n"
  say "All set. Next actions:"
  cat <<EOF
  * Tail logs        :  bash scripts/test-local.sh logs
  * Re-check counts  :  bash scripts/test-local.sh counts
  * Stop the MCP     :  bash scripts/test-local.sh stop
  * Rebuild image    :  bash scripts/test-local.sh rebuild

  Endpoints:
    curl ${MCP_URL}/health
    curl ${MCP_URL}/api/v1/bootstrap/status

  Reminder: Mongo is independent. This script never starts or stops it.
EOF
}

# ---- Subcommands -----------------------------------------------------------

cmd_run() {
  local crm_url="${1:-$DEFAULT_CRM_URL}"
  step_preflight "$crm_url"
  step_check_mongo
  step_build_image
  step_run_container
  step_bootstrap "$crm_url"
  step_verify
  step_counts
  step_summary
}

cmd_rebuild() {
  NO_CACHE=1 step_build_image
  if container_running; then
    say "Restarting container with fresh image"
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    step_run_container
    say "Re-bootstrap required (runtime config lives in memory): run the script again."
  fi
}

cmd_stop() {
  if container_exists; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    say "MCP container removed"
  else
    say "MCP container not present"
  fi
}

cmd_status() {
  if container_running; then
    printf "MCP container : RUNNING (%s -> %s)\n" "${CONTAINER_NAME}" "${MCP_URL}"
    printf "  image       : %s\n" "$(docker inspect -f '{{.Config.Image}}' "${CONTAINER_NAME}")"
    printf "  /health     : %s\n" "$(curl -s "${MCP_URL}/health" 2>/dev/null || echo 'unreachable')"
  elif container_exists; then
    printf "MCP container : stopped (exists but not running)\n"
  else
    printf "MCP container : not created\n"
  fi
  printf "Mongo URL     : %s\n" "$MONGODB_URL"
  if mongo_ping "$MONGODB_URL"; then
    printf "  reachable   : yes\n"
  else
    printf "  reachable   : NO (check MONGODB_URL, credentials, network)\n"
  fi
  printf "Default CRM   : %s\n" "$DEFAULT_CRM_URL"
}

cmd_logs() {
  container_exists || { err "container ${CONTAINER_NAME} not present"; exit 1; }
  docker logs -f --tail 100 "${CONTAINER_NAME}"
}

# ---- Entry ---------------------------------------------------------------
subcmd="${1:-run}"
shift || true
case "$subcmd" in
  run)      cmd_run "${1:-}" ;;
  rebuild)  cmd_rebuild ;;
  stop)     cmd_stop ;;
  status)   cmd_status ;;
  counts)   step_counts ;;
  logs)     cmd_logs ;;
  http://*|https://*) cmd_run "$subcmd" ;;
  *)
    err "Unknown command: $subcmd"
    echo "Usage:"
    echo "  bash scripts/test-local.sh [run] [CRM_URL]"
    echo "  bash scripts/test-local.sh [rebuild|stop|status|counts|logs]"
    echo ""
    echo "Examples:"
    echo "  bash scripts/test-local.sh                                    # local dev CRM"
    echo "  bash scripts/test-local.sh run https://leadpulse.projexlight.com"
    echo "  bash scripts/test-local.sh https://leadpulse.projexlight.com  # shorthand"
    echo ""
    echo "Mongo is expected to be running already. Override with:"
    echo "  MONGODB_URL='mongodb+srv://user:pass@cluster...' bash scripts/test-local.sh"
    exit 1
    ;;
esac

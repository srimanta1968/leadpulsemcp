#!/bin/bash
#===============================================================================
# ProjexLight SUT Connectivity Pre-Flight Check
#===============================================================================
# Verifies that the System Under Test (SUT) — the web app being tested — is:
#   1. Actually running on the host
#   2. Bound to 0.0.0.0 (not 127.0.0.1) so Docker containers can reach it
#   3. Reachable from inside the running MCP container via host.docker.internal
#
# Used by:
#   - run-all-tests.sh      (before launching UI / API / unified test runs)
#   - pre-push git hook     (before the Dev MCP pre-push API test sweep)
#   - manual diagnostic     (run directly: ./mcp-server/check-sut.sh [API_URL])
#
# Exit codes:
#   0 — SUT is reachable, ready to run tests
#   1 — SUT is not running or unreachable
#   2 — SUT is running but bound to loopback (will fail from container)
#   3 — SUT binding is OK but container cannot reach it (firewall suspected)
#
# Environment variables:
#   SUT_BASE_URL     — primary URL to probe (default: http://localhost:3000)
#   SUT_API_URL      — secondary URL to probe (default: $API_BASE_URL or empty)
#   SUT_PROBE_PATH   — health endpoint path relative to URL (default: / — any
#                      HTTP response proves TCP connectivity)
#   SUT_CONTAINER    — MCP container name to test from (default: projexlight-test-mcp)
#   SUT_CHECK_STRICT — if "true", fail fast on any warning (default: false)
#
# See SUT_SETUP_GUIDE.md for framework-specific binding instructions.
#===============================================================================

# Do NOT set -e — we handle errors explicitly and keep running so the
# user sees every diagnostic line before we decide pass/fail.

# Colors (only if stdout is a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    CYAN=''
    BOLD=''
    NC=''
fi

# Script/project paths — used to point users at the setup guide
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT_GUIDE_PATH="$SCRIPT_DIR/docs/SUT_SETUP_GUIDE.md"

# Resolved runtime config
SUT_BASE_URL="${SUT_BASE_URL:-${BASE_URL:-http://localhost:3000}}"
SUT_API_URL="${SUT_API_URL:-${API_BASE_URL:-}}"
SUT_PROBE_PATH="${SUT_PROBE_PATH:-/}"
SUT_CONTAINER="${SUT_CONTAINER:-projexlight-test-mcp}"
SUT_CHECK_STRICT="${SUT_CHECK_STRICT:-false}"

# Allow overriding by passing an argument (e.g., check-sut.sh http://localhost:5000)
if [ -n "${1:-}" ]; then
    SUT_BASE_URL="$1"
fi

say() { printf '%b\n' "$*"; }
err() { printf '%b\n' "${RED}$*${NC}" >&2; }
ok()  { printf '%b\n' "${GREEN}$*${NC}"; }
warn() { printf '%b\n' "${YELLOW}$*${NC}"; }

print_setup_guide_pointer() {
    echo ""
    say "${BOLD}${CYAN}────────────────────────────────────────────────────────────────${NC}"
    say "${BOLD}  How to fix: start your SUT on 0.0.0.0${NC}"
    say "${BOLD}${CYAN}────────────────────────────────────────────────────────────────${NC}"
    echo ""
    say "  Your app must be bound to ${BOLD}0.0.0.0${NC} (all interfaces), not"
    say "  ${BOLD}127.0.0.1${NC} (loopback). Docker containers cannot reach a loopback-"
    say "  bound socket on the host, even via ${BOLD}host.docker.internal${NC}."
    echo ""
    say "  Framework-specific commands are in:"
    say "    ${CYAN}${SUT_GUIDE_PATH}${NC}"
    echo ""
    say "  Quick reference for common frameworks:"
    say "    • ${BOLD}Express${NC}:     app.listen(3005)                ${GREEN}# Node default is 0.0.0.0${NC}"
    say "    • ${BOLD}Vite${NC}:        npm run dev -- --host"
    say "    • ${BOLD}Next.js${NC}:     next dev -H 0.0.0.0"
    say "    • ${BOLD}CRA${NC}:         HOST=0.0.0.0 npm start"
    say "    • ${BOLD}Angular${NC}:     ng serve --host 0.0.0.0 --disable-host-check"
    say "    • ${BOLD}Django${NC}:      python manage.py runserver 0.0.0.0:8000"
    say "    • ${BOLD}Flask${NC}:       flask run --host=0.0.0.0"
    say "    • ${BOLD}FastAPI${NC}:     uvicorn main:app --host 0.0.0.0 --port 8000"
    say "    • ${BOLD}Spring Boot${NC}: (safe by default — 0.0.0.0)"
    say "    • ${BOLD}Go${NC}:          http.ListenAndServe(\":8080\", nil)  ${GREEN}# :port = 0.0.0.0${NC}"
    say "    • ${BOLD}Rails${NC}:       rails server -b 0.0.0.0"
    say "    • ${BOLD}Nuxt${NC}:        nuxt dev --host 0.0.0.0"
    echo ""
    say "  Open the full guide for verification commands, firewall setup,"
    say "  and a troubleshooting flowchart:"
    say "    ${CYAN}cat ${SUT_GUIDE_PATH}${NC}"
    say "${BOLD}${CYAN}────────────────────────────────────────────────────────────────${NC}"
    echo ""
}

# Extract host:port from a URL like http://localhost:3005/path
parse_url() {
    local url="$1"
    # strip scheme
    local rest="${url#*://}"
    # strip path
    rest="${rest%%/*}"
    # split host:port
    local host="${rest%%:*}"
    local port="${rest##*:}"
    if [ "$host" = "$port" ]; then
        # no explicit port — assume 80 for http, 443 for https
        if [[ "$url" == https://* ]]; then
            port=443
        else
            port=80
        fi
    fi
    echo "$host:$port"
}

# Step 1: probe SUT from the host side (quickest and most common failure mode)
probe_host_side() {
    local url="$1"
    local probe_url="${url%/}${SUT_PROBE_PATH}"

    # -s silent, -f fail on HTTP errors, -I head request, --max-time cap
    # We accept ANY HTTP response (even 404/405) as proof of TCP connectivity.
    local http_code
    http_code="$(curl -o /dev/null -s -w '%{http_code}' --max-time 3 -I "$probe_url" 2>/dev/null || echo 000)"

    if [ "$http_code" = "000" ]; then
        return 1
    fi
    return 0
}

# Step 2: probe SUT from inside the MCP container via host.docker.internal
probe_container_side() {
    local url="$1"
    # Rewrite localhost → host.docker.internal so the probe reaches the host
    local container_url="${url/localhost/host.docker.internal}"
    container_url="${container_url/127.0.0.1/host.docker.internal}"
    local probe_url="${container_url%/}${SUT_PROBE_PATH}"

    # Short timeout — container networking is fast, 3 seconds is ample
    docker exec "$SUT_CONTAINER" curl -s -o /dev/null --max-time 3 -I "$probe_url" 2>/dev/null
    return $?
}

# Is the given hostname something that resolves to the local machine?
#
# For LOCAL hostnames (localhost, 127.0.0.1, ::1, host.docker.internal),
# we run the full pre-flight: netstat bind-address check + host HTTP probe
# + container HTTP probe. The bind-address check catches the common
# "SUT bound to 127.0.0.1 only" footgun that breaks Docker → host traffic.
#
# For REMOTE hostnames (e.g., https://qa.example.com, http://192.168.1.50:5173,
# http://coworker-mac.local:3000), the netstat check is meaningless — the
# SUT is on another machine, and the ONLY meaningful test is whether the
# container can HTTP-reach the URL. We skip the bind check and go straight
# to HTTP probing.
is_local_host() {
    local host="$1"
    case "$host" in
        localhost|127.0.0.1|::1|host.docker.internal|0.0.0.0)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Step 3: check whether a port is bound to loopback vs all-interfaces
# Returns: "0.0.0.0" | "127.0.0.1" | "unknown" | "not_listening"
get_bind_address() {
    local port="$1"

    if command -v ss &>/dev/null; then
        # Linux — ss is the modern tool
        local line
        line="$(ss -tlnH "sport = :$port" 2>/dev/null | head -1)"
        if [ -z "$line" ]; then
            echo "not_listening"; return
        fi
        case "$line" in
            *0.0.0.0:$port*|*\\*:$port*|*"[::]:$port"*) echo "0.0.0.0" ;;
            *127.0.0.1:$port*|*"[::1]:$port"*) echo "127.0.0.1" ;;
            *) echo "unknown" ;;
        esac
    elif command -v lsof &>/dev/null; then
        # macOS — lsof is universally available
        local line
        line="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1)"
        if [ -z "$line" ]; then
            echo "not_listening"; return
        fi
        case "$line" in
            *"*:$port"*|*"0.0.0.0:$port"*) echo "0.0.0.0" ;;
            *"127.0.0.1:$port"*|*"[::1]:$port"*) echo "127.0.0.1" ;;
            *) echo "unknown" ;;
        esac
    elif command -v netstat &>/dev/null; then
        # Windows / Cygwin / MinGW — netstat -ano
        local line
        line="$(netstat -ano 2>/dev/null | grep -E "LISTENING|LISTEN" | grep -E "[: ]$port[[:space:]]" | head -1)"
        if [ -z "$line" ]; then
            echo "not_listening"; return
        fi
        case "$line" in
            *"0.0.0.0:$port"*|*"[::]:$port"*) echo "0.0.0.0" ;;
            *"127.0.0.1:$port"*|*"[::1]:$port"*) echo "127.0.0.1" ;;
            *) echo "unknown" ;;
        esac
    else
        echo "unknown"
    fi
}

main() {
    say ""
    say "${BOLD}${BLUE}==============================================${NC}"
    say "${BOLD}${BLUE}  SUT Connectivity Pre-Flight${NC}"
    say "${BOLD}${BLUE}==============================================${NC}"
    say "  Primary URL: ${SUT_BASE_URL}"
    if [ -n "$SUT_API_URL" ]; then
        say "  API URL:     ${SUT_API_URL}"
    fi

    local any_failure=0

    for url_to_check in "$SUT_BASE_URL" "$SUT_API_URL"; do
        if [ -z "$url_to_check" ]; then continue; fi

        local host_port
        host_port="$(parse_url "$url_to_check")"
        local host="${host_port%:*}"
        local port="${host_port##*:}"

        echo ""
        say "  ${BOLD}→ Checking ${url_to_check}${NC}"

        # Two code paths: LOCAL (where we can introspect netstat) and
        # REMOTE (where we can't — we're testing an SUT on another host
        # or in the cloud, so bind-address checks don't apply).
        if is_local_host "$host"; then
            # ==========================================================
            # LOCAL — SUT runs on this machine. Full pre-flight.
            # ==========================================================
            local bind_addr
            bind_addr="$(get_bind_address "$port")"
            case "$bind_addr" in
                not_listening)
                    err "    ✗ Nothing listening on port ${port}"
                    say "      ${YELLOW}Start your SUT first, then re-run.${NC}"
                    any_failure=1
                    ;;
                127.0.0.1)
                    err "    ✗ Port ${port} is bound to 127.0.0.1 (loopback only)"
                    say "      ${YELLOW}Docker containers cannot reach loopback-bound ports.${NC}"
                    say "      ${YELLOW}Restart your SUT with the right flag — see guide below.${NC}"
                    any_failure=2
                    ;;
                0.0.0.0)
                    ok "    ✓ Port ${port} is bound to 0.0.0.0 (all interfaces)"
                    ;;
                unknown)
                    warn "    ? Could not determine bind address for port ${port}"
                    say "      ${YELLOW}Continuing with TCP probe...${NC}"
                    ;;
            esac

            # If we already know it's loopback or not listening, skip the HTTP probes
            if [ "$bind_addr" = "not_listening" ] || [ "$bind_addr" = "127.0.0.1" ]; then
                continue
            fi

            # --- Host-side HTTP probe ---
            if probe_host_side "$url_to_check"; then
                ok "    ✓ Host-side HTTP probe succeeded"
            else
                err "    ✗ Host-side HTTP probe failed"
                say "      ${YELLOW}Port is listening but HTTP request got no response.${NC}"
                say "      ${YELLOW}Check that your SUT has booted fully.${NC}"
                any_failure=1
                continue
            fi

            # --- Container-side HTTP probe ---
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${SUT_CONTAINER}$"; then
                if probe_container_side "$url_to_check"; then
                    ok "    ✓ Container → host.docker.internal probe succeeded"
                else
                    err "    ✗ Container cannot reach host.docker.internal:${port}"
                    say "      ${YELLOW}Host bind looks OK but Docker container cannot reach it.${NC}"
                    say "      ${YELLOW}Most likely cause on Windows: Defender Firewall blocking${NC}"
                    say "      ${YELLOW}the port from the Docker Desktop vEthernet adapter.${NC}"
                    say "      ${YELLOW}See docs/SUT_SETUP_GUIDE.md → Windows Firewall.${NC}"
                    if [ $any_failure -lt 3 ]; then any_failure=3; fi
                fi
            else
                warn "    ? Container ${SUT_CONTAINER} not running — skipping container-side probe"
            fi
        else
            # ==========================================================
            # REMOTE — SUT is on another machine (LAN coworker, cloud
            # QA/staging/prod, anywhere non-local). Skip the bind check
            # because we can't netstat a box we don't control. Only the
            # container-side probe matters — the container is what will
            # actually run the tests, so that's the question that counts.
            # ==========================================================
            say "    ℹ Remote host — skipping local bind-address check"

            # --- Host-side HTTP probe (from YOUR machine) ---
            # This proves the SUT is reachable over the network from your
            # development machine. Catches DNS failures, firewall blocks,
            # and "I forgot to connect to the VPN" issues.
            if probe_host_side "$url_to_check"; then
                ok "    ✓ Host-side HTTP probe succeeded (from local machine)"
            else
                err "    ✗ Cannot reach ${url_to_check} from local machine"
                say "      ${YELLOW}Possible causes:${NC}"
                say "      ${YELLOW}  • DNS not resolving the hostname${NC}"
                say "      ${YELLOW}  • Firewall / VPN not connected${NC}"
                say "      ${YELLOW}  • Remote SUT is down${NC}"
                say "      ${YELLOW}  • TLS cert issue (try curl -k first)${NC}"
                any_failure=1
                continue
            fi

            # --- Container-side HTTP probe (the real test) ---
            # Containers have outbound internet by default, so this should
            # succeed if the host-side probe succeeded. If it doesn't, it
            # means Docker's egress is restricted (rare — corporate proxy,
            # custom network mode, etc.).
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${SUT_CONTAINER}$"; then
                if probe_container_side "$url_to_check"; then
                    ok "    ✓ Container → ${url_to_check%%/*} probe succeeded"
                else
                    err "    ✗ Container cannot reach ${url_to_check}"
                    say "      ${YELLOW}The host can reach it but the container can't.${NC}"
                    say "      ${YELLOW}Possible causes:${NC}"
                    say "      ${YELLOW}  • Docker's DNS not resolving (check container's /etc/resolv.conf)${NC}"
                    say "      ${YELLOW}  • Corporate proxy blocking outbound traffic${NC}"
                    say "      ${YELLOW}  • Docker network policy restricting egress${NC}"
                    if [ $any_failure -lt 3 ]; then any_failure=3; fi
                fi
            else
                warn "    ? Container ${SUT_CONTAINER} not running — skipping container-side probe"
            fi
        fi
    done

    echo ""
    if [ $any_failure -eq 0 ]; then
        say "${BOLD}${GREEN}==============================================${NC}"
        ok  "  ✓ SUT is ready — safe to run tests"
        say "${BOLD}${GREEN}==============================================${NC}"
        echo ""
        return 0
    fi

    say "${BOLD}${RED}==============================================${NC}"
    err "  ✗ SUT is not ready for testing"
    say "${BOLD}${RED}==============================================${NC}"
    print_setup_guide_pointer
    return $any_failure
}

main "$@"
exit $?

#!/bin/bash
#===============================================================================
# ProjexLight SUT Port Auto-Detection
#===============================================================================
# Detects the actual ports your SUT (system under test) is configured to run
# on by parsing framework config files, then updates tests/config/test-config.json
# so you don't have to edit port values by hand every time you restructure
# your project.
#
# Supports:
#   Backend  — any project with ./server/.env, ./backend/.env, or ./api/.env
#              containing a PORT=... line (Express, NestJS, Koa, Fastify, etc.)
#   Frontend — Vite (vite.config.ts|js), Next.js (next.config.js|ts + scripts),
#              Create React App (package.json scripts + HOST/PORT env),
#              Angular (angular.json), Nuxt (nuxt.config.ts|js),
#              SvelteKit (svelte.config.js), Astro (astro.config.mjs)
#
# For frameworks not listed here, the existing test-config.json values are
# left intact and you can override at runtime with:
#   UI_BASE_URL=http://localhost:8080 API_BASE_URL=http://localhost:9000 \
#     ./run-all-tests.sh api
#
# Usage:
#   Standalone:   ./auto-detect-ports.sh [PROJECT_ROOT]
#   From script:  source ./auto-detect-ports.sh
#                 auto_detect_ports "$PROJECT_ROOT"
#
# Environment variables:
#   SUT_AUTO_CONFIG=false    — disable auto-detection entirely
#   SUT_AUTO_WRITE=false     — detect and print only, don't modify test-config.json
#   SUT_AUTO_VERBOSE=true    — show what was checked, even misses
#===============================================================================

set -o pipefail

# Colors (only if stdout is a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

auto_detect_say()  { printf '%b\n' "$*"; }
auto_detect_warn() { printf '%b\n' "${YELLOW}$*${NC}"; }
auto_detect_ok()   { printf '%b\n' "${GREEN}$*${NC}"; }
auto_detect_err()  { printf '%b\n' "${RED}$*${NC}" >&2; }

# -----------------------------------------------------------------------------
# BACKEND DETECTION
# -----------------------------------------------------------------------------
# Looks for a .env file in conventional locations and extracts PORT=.
# Returns the port number on stdout, or empty string if not found.
detect_backend_port() {
    local project_root="$1"
    local candidates=(
        "$project_root/server/.env"
        "$project_root/backend/.env"
        "$project_root/api/.env"
        "$project_root/apps/server/.env"
        "$project_root/apps/backend/.env"
        "$project_root/apps/api/.env"
    )

    for env_file in "${candidates[@]}"; do
        if [ -f "$env_file" ]; then
            local port
            # Match `PORT=3005` or `PORT = 3005` or `PORT='3005'` — strip
            # quotes, whitespace, and trailing comments.
            port="$(grep -E '^[[:space:]]*PORT[[:space:]]*=' "$env_file" 2>/dev/null \
                | head -1 \
                | sed -E 's/^[[:space:]]*PORT[[:space:]]*=[[:space:]]*//; s/[[:space:]]*#.*$//; s/["'"'"']//g; s/[[:space:]]*$//')"
            if [ -n "$port" ]; then
                echo "$port"
                # Return which file we found it in via stderr for logging
                echo "[DEBUG] Backend port $port from $env_file" >&2
                return 0
            fi
        fi
    done

    # Fallback: check application.properties / application.yml for Spring Boot
    for props in "$project_root/server/src/main/resources/application.properties" \
                 "$project_root/src/main/resources/application.properties" \
                 "$project_root/backend/src/main/resources/application.properties"; do
        if [ -f "$props" ]; then
            local port
            port="$(grep -E '^[[:space:]]*server\.port[[:space:]]*=' "$props" 2>/dev/null \
                | head -1 | sed -E 's/^.*=[[:space:]]*//; s/[[:space:]]*$//')"
            if [ -n "$port" ]; then
                echo "$port"
                echo "[DEBUG] Backend port $port from $props (Spring Boot)" >&2
                return 0
            fi
        fi
    done

    return 1
}

# -----------------------------------------------------------------------------
# FRONTEND DETECTION
# -----------------------------------------------------------------------------
# Tries frontend frameworks in priority order. The first match wins.
# Returns the port on stdout; the framework name on stderr as a debug hint.
detect_frontend_port() {
    local project_root="$1"

    # Common frontend roots
    local frontend_roots=(
        "$project_root/client"
        "$project_root/frontend"
        "$project_root/web"
        "$project_root/ui"
        "$project_root/app"
        "$project_root"
    )

    local fe_root=""
    for candidate in "${frontend_roots[@]}"; do
        if [ -f "$candidate/package.json" ]; then
            fe_root="$candidate"
            break
        fi
    done

    if [ -z "$fe_root" ]; then
        return 1
    fi

    # ---- 1. Vite: vite.config.ts / vite.config.js — server.port
    for cfg in "$fe_root/vite.config.ts" "$fe_root/vite.config.js" \
               "$fe_root/vite.config.mts" "$fe_root/vite.config.mjs"; do
        if [ -f "$cfg" ]; then
            local port
            # Match `port: 5173` inside a `server: { ... }` block.
            # Not a full JS parser — looks for "port: <number>" anywhere,
            # which works for standard configs.
            port="$(grep -E 'port[[:space:]]*:[[:space:]]*[0-9]+' "$cfg" 2>/dev/null \
                | head -1 \
                | sed -E 's/.*port[[:space:]]*:[[:space:]]*([0-9]+).*/\1/')"
            if [ -n "$port" ]; then
                echo "$port"
                echo "[DEBUG] Frontend port $port from $cfg (Vite)" >&2
                return 0
            fi
            # Vite default if config file exists but no explicit port
            echo "5173"
            echo "[DEBUG] Frontend port 5173 from $cfg (Vite default)" >&2
            return 0
        fi
    done

    # ---- 2. Next.js: next.config.js / scripts — `next dev -p <port>`
    for cfg in "$fe_root/next.config.js" "$fe_root/next.config.ts" \
               "$fe_root/next.config.mjs"; do
        if [ -f "$cfg" ]; then
            # Next.js defaults to 3000 unless -p / --port in package.json dev script
            local port
            port="$(grep -oE '"dev"[[:space:]]*:[[:space:]]*"[^"]*(-p|--port)[[:space:]]+[0-9]+' \
                    "$fe_root/package.json" 2>/dev/null \
                | head -1 \
                | grep -oE '[0-9]+$')"
            if [ -n "$port" ]; then
                echo "$port"
                echo "[DEBUG] Frontend port $port from $fe_root/package.json (Next.js dev script)" >&2
                return 0
            fi
            echo "3000"
            echo "[DEBUG] Frontend port 3000 from $cfg (Next.js default)" >&2
            return 0
        fi
    done

    # ---- 3. Angular: angular.json — architect.serve.options.port
    if [ -f "$fe_root/angular.json" ]; then
        local port
        # Simple grep for `"port": NNNN` inside serve options
        port="$(grep -oE '"port"[[:space:]]*:[[:space:]]*[0-9]+' "$fe_root/angular.json" 2>/dev/null \
            | head -1 \
            | grep -oE '[0-9]+$')"
        if [ -n "$port" ]; then
            echo "$port"
            echo "[DEBUG] Frontend port $port from $fe_root/angular.json (Angular)" >&2
            return 0
        fi
        echo "4200"
        echo "[DEBUG] Frontend port 4200 from $fe_root/angular.json (Angular default)" >&2
        return 0
    fi

    # ---- 4. Nuxt: nuxt.config.ts|js — devServer.port
    for cfg in "$fe_root/nuxt.config.ts" "$fe_root/nuxt.config.js" \
               "$fe_root/nuxt.config.mjs"; do
        if [ -f "$cfg" ]; then
            local port
            port="$(grep -E 'port[[:space:]]*:[[:space:]]*[0-9]+' "$cfg" 2>/dev/null \
                | head -1 | sed -E 's/.*port[[:space:]]*:[[:space:]]*([0-9]+).*/\1/')"
            if [ -n "$port" ]; then
                echo "$port"
                echo "[DEBUG] Frontend port $port from $cfg (Nuxt)" >&2
                return 0
            fi
            echo "3000"
            echo "[DEBUG] Frontend port 3000 from $cfg (Nuxt default)" >&2
            return 0
        fi
    done

    # ---- 5. SvelteKit: svelte.config.js — typically inherits Vite
    if [ -f "$fe_root/svelte.config.js" ]; then
        echo "5173"
        echo "[DEBUG] Frontend port 5173 from $fe_root/svelte.config.js (SvelteKit via Vite)" >&2
        return 0
    fi

    # ---- 6. Astro: astro.config.mjs — server.port
    if [ -f "$fe_root/astro.config.mjs" ] || [ -f "$fe_root/astro.config.ts" ]; then
        echo "4321"
        echo "[DEBUG] Frontend port 4321 from astro.config (Astro default)" >&2
        return 0
    fi

    # ---- 7. Create React App: package.json scripts with HOST/PORT
    if [ -f "$fe_root/package.json" ]; then
        if grep -qE '"react-scripts"' "$fe_root/package.json" 2>/dev/null; then
            local port
            port="$(grep -oE 'PORT[[:space:]]*=[[:space:]]*[0-9]+' "$fe_root/package.json" 2>/dev/null \
                | head -1 | grep -oE '[0-9]+$')"
            if [ -n "$port" ]; then
                echo "$port"
                echo "[DEBUG] Frontend port $port from $fe_root/package.json (CRA)" >&2
                return 0
            fi
            echo "3000"
            echo "[DEBUG] Frontend port 3000 from $fe_root/package.json (CRA default)" >&2
            return 0
        fi
    fi

    return 1
}

# -----------------------------------------------------------------------------
# VERIFY: does something actually listen on this port?
# -----------------------------------------------------------------------------
is_port_listening() {
    local port="$1"

    if command -v ss &>/dev/null; then
        ss -tln 2>/dev/null | grep -qE "[:.]$port\b"
    elif command -v lsof &>/dev/null; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | grep -q LISTEN
    elif command -v netstat &>/dev/null; then
        netstat -ano 2>/dev/null | grep -E "[:.]$port\b" | grep -qE "LISTENING|LISTEN"
    else
        return 2   # no tool available
    fi
}

# -----------------------------------------------------------------------------
# WRITE: update test-config.json with detected values (via jq)
# -----------------------------------------------------------------------------
update_test_config() {
    local config_file="$1"
    local env_name="$2"
    local base_url="$3"
    local api_url="$4"

    if [ ! -f "$config_file" ]; then
        auto_detect_warn "  Config file not found: $config_file — skipping write"
        return 1
    fi

    if ! command -v jq &>/dev/null; then
        auto_detect_warn "  jq not installed — cannot safely rewrite test-config.json"
        auto_detect_warn "  Manual update needed:"
        auto_detect_warn "    .environments.${env_name}.baseUrl = \"$base_url\""
        auto_detect_warn "    .environments.${env_name}.apiUrl  = \"$api_url\""
        return 1
    fi

    local current_base current_api
    current_base="$(jq -r ".environments.${env_name}.baseUrl // \"\"" "$config_file")"
    current_api="$(jq -r  ".environments.${env_name}.apiUrl  // \"\"" "$config_file")"

    if [ "$current_base" = "$base_url" ] && [ "$current_api" = "$api_url" ]; then
        auto_detect_ok "  test-config.json already matches detected ports — no changes needed"
        return 0
    fi

    local tmp="$config_file.tmp.$$"
    jq --arg env "$env_name" --arg base "$base_url" --arg api "$api_url" \
       '.environments[$env].baseUrl = $base | .environments[$env].apiUrl = $api' \
       "$config_file" > "$tmp" && mv "$tmp" "$config_file"

    if [ "$current_base" != "$base_url" ]; then
        auto_detect_ok "  Updated baseUrl: $current_base → $base_url"
    fi
    if [ "$current_api" != "$api_url" ]; then
        auto_detect_ok "  Updated apiUrl:  $current_api → $api_url"
    fi
}

# -----------------------------------------------------------------------------
# MAIN: detect, verify, write
# -----------------------------------------------------------------------------
auto_detect_ports() {
    local project_root="${1:-$(pwd)}"
    local config_file="$project_root/tests/config/test-config.json"
    local env_name="${SUT_AUTO_ENV:-development}"

    if [ "${SUT_AUTO_CONFIG:-true}" = "false" ]; then
        return 0
    fi

    auto_detect_say ""
    auto_detect_say "${BOLD}${BLUE}==============================================${NC}"
    auto_detect_say "${BOLD}${BLUE}  SUT Port Auto-Detection${NC}"
    auto_detect_say "${BOLD}${BLUE}==============================================${NC}"
    auto_detect_say "  Project root: $project_root"
    auto_detect_say "  Config file:  $config_file"
    auto_detect_say "  Environment:  $env_name"
    auto_detect_say ""

    # Detect (send debug output through stderr by using 2>/dev/null when verbose off)
    local backend_port frontend_port
    local verbose_redirect="/dev/null"
    if [ "${SUT_AUTO_VERBOSE:-false}" = "true" ]; then
        verbose_redirect="/dev/stderr"
    fi

    backend_port="$(detect_backend_port "$project_root" 2>$verbose_redirect)"
    frontend_port="$(detect_frontend_port "$project_root" 2>$verbose_redirect)"

    if [ -z "$backend_port" ] && [ -z "$frontend_port" ]; then
        auto_detect_warn "  No framework config files detected."
        auto_detect_warn "  Leaving test-config.json unchanged."
        auto_detect_warn "  Override at runtime with UI_BASE_URL / API_BASE_URL env vars."
        return 0
    fi

    # Build URLs — always use localhost; run-all-tests.sh will translate
    # to host.docker.internal for Docker container access later.
    local base_url="" api_url=""
    if [ -n "$frontend_port" ]; then
        base_url="http://localhost:$frontend_port"
        auto_detect_ok "  ✓ Frontend port: $frontend_port → $base_url"
    else
        auto_detect_warn "  ? Frontend port: not detected"
    fi
    if [ -n "$backend_port" ]; then
        api_url="http://localhost:$backend_port"
        auto_detect_ok "  ✓ Backend port:  $backend_port → $api_url"
    else
        auto_detect_warn "  ? Backend port:  not detected"
    fi

    # Verify ports are actually listening (soft — warn, don't block)
    if [ -n "$frontend_port" ]; then
        if is_port_listening "$frontend_port"; then
            auto_detect_ok "  ✓ Port $frontend_port is listening on this host"
        else
            auto_detect_warn "  ⚠ Port $frontend_port is NOT listening — frontend may not be started yet"
        fi
    fi
    if [ -n "$backend_port" ]; then
        if is_port_listening "$backend_port"; then
            auto_detect_ok "  ✓ Port $backend_port is listening on this host"
        else
            auto_detect_warn "  ⚠ Port $backend_port is NOT listening — backend may not be started yet"
        fi
    fi

    auto_detect_say ""

    # If caller only wants detection, don't write
    if [ "${SUT_AUTO_WRITE:-true}" = "false" ]; then
        auto_detect_warn "  SUT_AUTO_WRITE=false — skipping test-config.json update"
        return 0
    fi

    # If either URL is missing, don't partially update (keep both in sync)
    if [ -z "$base_url" ] || [ -z "$api_url" ]; then
        auto_detect_warn "  Only one of (frontend, backend) detected — skipping test-config.json update"
        auto_detect_warn "  Both URLs are kept as-is to avoid partial-write drift."
        return 0
    fi

    auto_detect_say "  Updating ${config_file}..."
    update_test_config "$config_file" "$env_name" "$base_url" "$api_url"
    auto_detect_say ""
}

# If invoked as a script (not sourced), run with the first arg as project root.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    auto_detect_ports "${1:-$(pwd)}"
    exit $?
fi

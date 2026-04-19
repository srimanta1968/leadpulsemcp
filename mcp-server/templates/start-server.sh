#!/bin/bash
#
# ProjexLight Unified Server Startup Script
# ============================================
# This script is generated during CLI Export (Task 0) and customized for your project
# It handles:
#   - Loading environment variables from .env
#   - Installing dependencies
#   - Running compilation/build
#   - Starting the development server
#   - Health check validation
#
# Exit codes:
#   0 = Server started successfully
#   1 = Dependency installation failed
#   2 = Build/compilation failed
#   3 = Server failed to start
#   4 = Health check failed
#
# This template will be customized based on your project configuration

set -e

# ============================================================
# Configuration (Auto-generated based on project)
# ============================================================

# These values are replaced during CLI export generation
PROJECT_TYPE="${PROJECT_TYPE:-{{PROJECT_TYPE}}}"
FRAMEWORK="${FRAMEWORK:-{{FRAMEWORK}}}"
LANGUAGE="${LANGUAGE:-{{LANGUAGE}}}"
SERVER_PORT="${SERVER_PORT:-{{SERVER_PORT}}}"
HEALTH_ENDPOINT="${HEALTH_ENDPOINT:-{{HEALTH_ENDPOINT}}}"
BUILD_COMMAND="${BUILD_COMMAND:-{{BUILD_COMMAND}}}"
START_COMMAND="${START_COMMAND:-{{START_COMMAND}}}"
INSTALL_COMMAND="${INSTALL_COMMAND:-{{INSTALL_COMMAND}}}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Log file
LOG_FILE="${PROJECT_ROOT}/.projexlight/logs/server-startup.log"
mkdir -p "$(dirname "$LOG_FILE")"

# PID file for cleanup
PID_FILE="${PROJECT_ROOT}/.projexlight/server.pid"

log() {
    echo -e "$1"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE" 2>/dev/null || true
}

log_error() {
    echo -e "${RED}$1${NC}" >&2
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1" >> "$LOG_FILE" 2>/dev/null || true
}

# ============================================================
# STEP 1: Load environment variables
# ============================================================
load_env() {
    log "${BLUE}📁 Loading environment variables...${NC}"

    # Check multiple possible .env locations
    ENV_FILES=(
        "${PROJECT_ROOT}/.env"
        "${PROJECT_ROOT}/server/.env"
        "${PROJECT_ROOT}/backend/.env"
        "${PROJECT_ROOT}/.env.local"
        "${PROJECT_ROOT}/.env.development"
    )

    ENV_LOADED=false
    for env_file in "${ENV_FILES[@]}"; do
        if [ -f "$env_file" ]; then
            log "   Loading: $env_file"
            set -a
            source "$env_file"
            set +a
            ENV_LOADED=true
        fi
    done

    if [ "$ENV_LOADED" = false ]; then
        log "${YELLOW}   ⚠️  No .env file found${NC}"
    fi

    # Override port if specified in .env
    if [ -n "$PORT" ]; then
        SERVER_PORT="$PORT"
        log "   Using PORT from .env: $SERVER_PORT"
    fi

    if [ -n "$DATABASE_URL" ]; then
        log "   DATABASE_URL configured"
    fi
}

# ============================================================
# STEP 2: Install dependencies
# ============================================================
install_dependencies() {
    log "${BLUE}📦 Installing dependencies...${NC}"

    cd "$PROJECT_ROOT"

    if [ -z "$INSTALL_COMMAND" ] || [ "$INSTALL_COMMAND" = "{{INSTALL_COMMAND}}" ]; then
        log "   Skipping (no install command configured)"
        return 0
    fi

    # Check if dependencies are already installed
    case "$PROJECT_TYPE" in
        node|monorepo)
            if [ -d "node_modules" ] && [ "$(find node_modules -maxdepth 1 -type d | wc -l)" -gt 10 ]; then
                log "   Dependencies already installed (node_modules exists)"
                return 0
            fi
            ;;
        python)
            # Python venv check
            if [ -d "venv" ] || [ -d ".venv" ]; then
                log "   Virtual environment found"
            fi
            ;;
    esac

    log "   Running: $INSTALL_COMMAND"
    if ! eval "$INSTALL_COMMAND" >> "$LOG_FILE" 2>&1; then
        log_error "❌ Dependency installation failed!"
        log_error "   Check log: $LOG_FILE"
        tail -20 "$LOG_FILE" | head -10
        return 1
    fi

    log "${GREEN}   ✓ Dependencies installed${NC}"
    return 0
}

# ============================================================
# STEP 3: Build/Compile project
# ============================================================
build_project() {
    log "${BLUE}🔨 Building project...${NC}"

    cd "$PROJECT_ROOT"

    if [ -z "$BUILD_COMMAND" ] || [ "$BUILD_COMMAND" = "{{BUILD_COMMAND}}" ]; then
        log "   Skipping (no build command configured)"
        return 0
    fi

    log "   Running: $BUILD_COMMAND"

    BUILD_OUTPUT=$(mktemp)
    if ! eval "$BUILD_COMMAND" > "$BUILD_OUTPUT" 2>&1; then
        log_error "❌ Build/compilation failed!"
        log_error ""

        # Extract and display compilation errors
        if grep -q "error TS" "$BUILD_OUTPUT"; then
            log_error "TypeScript compilation errors:"
            grep "error TS" "$BUILD_OUTPUT" | head -10
        elif grep -q "SyntaxError\|IndentationError" "$BUILD_OUTPUT"; then
            log_error "Python syntax errors:"
            grep -A 2 "SyntaxError\|IndentationError" "$BUILD_OUTPUT" | head -15
        elif grep -q "error:" "$BUILD_OUTPUT"; then
            log_error "Compilation errors:"
            grep "error:" "$BUILD_OUTPUT" | head -10
        else
            tail -20 "$BUILD_OUTPUT" | head -15
        fi

        cat "$BUILD_OUTPUT" >> "$LOG_FILE"
        rm -f "$BUILD_OUTPUT"
        return 2
    fi

    cat "$BUILD_OUTPUT" >> "$LOG_FILE"
    rm -f "$BUILD_OUTPUT"

    log "${GREEN}   ✓ Build successful${NC}"
    return 0
}

# ============================================================
# STEP 4: Start server
# ============================================================
start_server() {
    log "${BLUE}🚀 Starting development server...${NC}"

    cd "$PROJECT_ROOT"

    if [ -z "$START_COMMAND" ] || [ "$START_COMMAND" = "{{START_COMMAND}}" ]; then
        log_error "❌ No start command configured!"
        return 3
    fi

    # Check if server is already running
    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
        if kill -0 "$OLD_PID" 2>/dev/null; then
            log "   Server already running (PID: $OLD_PID)"
            return 0
        fi
        rm -f "$PID_FILE"
    fi

    # Check if port is already in use
    if command -v lsof &> /dev/null; then
        if lsof -i ":$SERVER_PORT" -P -n | grep LISTEN > /dev/null 2>&1; then
            log "${YELLOW}   ⚠️  Port $SERVER_PORT is already in use${NC}"
            log "   Another process might be running"
            # Still proceed to health check
            return 0
        fi
    fi

    log "   Running: $START_COMMAND"
    log "   Server port: $SERVER_PORT"

    # Start server in background
    nohup bash -c "$START_COMMAND" >> "$LOG_FILE" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_FILE"

    log "   Server PID: $SERVER_PID"

    # Wait for server to initialize
    log "   Waiting for server to start..."

    STARTUP_TIMEOUT=60  # seconds
    STARTUP_CHECK_INTERVAL=2  # seconds
    ELAPSED=0

    while [ $ELAPSED -lt $STARTUP_TIMEOUT ]; do
        sleep $STARTUP_CHECK_INTERVAL
        ELAPSED=$((ELAPSED + STARTUP_CHECK_INTERVAL))

        # Check if process is still running
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            log_error "❌ Server process died during startup!"
            log_error "   Check log for errors: $LOG_FILE"
            tail -30 "$LOG_FILE" | head -20
            return 3
        fi

        # Check if port is now responding
        if curl -s --max-time 2 "http://localhost:$SERVER_PORT" > /dev/null 2>&1; then
            log "${GREEN}   ✓ Server started successfully (${ELAPSED}s)${NC}"
            return 0
        fi

        echo -ne "   Waiting... ${ELAPSED}/${STARTUP_TIMEOUT}s\r"
    done

    log_error "❌ Server failed to start within ${STARTUP_TIMEOUT}s"
    tail -20 "$LOG_FILE"

    # Cleanup
    kill "$SERVER_PID" 2>/dev/null || true
    rm -f "$PID_FILE"
    return 3
}

# ============================================================
# STEP 5: Health check
# ============================================================
health_check() {
    log "${BLUE}🏥 Running health check...${NC}"

    if [ -z "$HEALTH_ENDPOINT" ] || [ "$HEALTH_ENDPOINT" = "{{HEALTH_ENDPOINT}}" ]; then
        # Default health check - just check if port is responding
        HEALTH_ENDPOINT="http://localhost:$SERVER_PORT"
    fi

    log "   Checking: $HEALTH_ENDPOINT"

    # Try health check with retries
    MAX_RETRIES=5
    for i in $(seq 1 $MAX_RETRIES); do
        RESPONSE=$(curl -s --max-time 10 -o /dev/null -w "%{http_code}" "$HEALTH_ENDPOINT" 2>/dev/null || echo "000")

        if [ "$RESPONSE" = "200" ] || [ "$RESPONSE" = "204" ] || [ "$RESPONSE" = "301" ] || [ "$RESPONSE" = "302" ]; then
            log "${GREEN}   ✓ Health check passed (HTTP $RESPONSE)${NC}"
            return 0
        fi

        if [ "$RESPONSE" != "000" ]; then
            log "   Attempt $i/$MAX_RETRIES: HTTP $RESPONSE"
        else
            log "   Attempt $i/$MAX_RETRIES: Connection refused"
        fi

        sleep 2
    done

    log_error "❌ Health check failed after $MAX_RETRIES attempts"
    return 4
}

# ============================================================
# STEP 6: Report status
# ============================================================
report_status() {
    local exit_code=$1

    echo ""
    echo "============================================"

    case $exit_code in
        0)
            log "${GREEN}✅ Server is running and healthy${NC}"
            log "   Port: $SERVER_PORT"
            log "   Health: $HEALTH_ENDPOINT"
            if [ -f "$PID_FILE" ]; then
                log "   PID: $(cat "$PID_FILE")"
            fi
            ;;
        1)
            log_error "❌ FAILED: Dependency installation"
            log_error "   Fix: Check $LOG_FILE for errors"
            ;;
        2)
            log_error "❌ FAILED: Build/compilation"
            log_error "   Fix: Resolve compilation errors before pushing"
            ;;
        3)
            log_error "❌ FAILED: Server startup"
            log_error "   Fix: Check server configuration and logs"
            ;;
        4)
            log_error "❌ FAILED: Health check"
            log_error "   Fix: Server running but not responding correctly"
            ;;
    esac

    echo "============================================"
    echo ""
}

# ============================================================
# Cleanup function
# ============================================================
cleanup() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            log "Stopping server (PID: $PID)"
            kill "$PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
}

# ============================================================
# Main execution
# ============================================================
main() {
    log "${BLUE}════════════════════════════════════════════${NC}"
    log "${BLUE}  ProjexLight Server Startup Script${NC}"
    log "${BLUE}════════════════════════════════════════════${NC}"
    log "Project Type: $PROJECT_TYPE"
    log "Framework: $FRAMEWORK"
    log "Language: $LANGUAGE"
    log ""

    # Execute steps
    load_env || exit 1

    if ! install_dependencies; then
        report_status 1
        exit 1
    fi

    if ! build_project; then
        report_status 2
        exit 2
    fi

    if ! start_server; then
        report_status 3
        exit 3
    fi

    if ! health_check; then
        report_status 4
        exit 4
    fi

    report_status 0
    exit 0
}

# Handle script arguments
case "${1:-start}" in
    start)
        main
        ;;
    stop)
        cleanup
        log "Server stopped"
        ;;
    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                log "Server running (PID: $PID)"
                health_check
            else
                log "Server not running (stale PID file)"
                rm -f "$PID_FILE"
            fi
        else
            log "Server not running"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status}"
        exit 1
        ;;
esac

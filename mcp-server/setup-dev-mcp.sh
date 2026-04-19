#!/bin/bash
#===============================================================================
# ProjexLight DEV MCP Server Setup Script
#===============================================================================
# This script manages the Development MCP Server Docker container.
# The DEV MCP provides code analysis, review, and development assistance.
#
# Usage:
#   ./setup-dev-mcp.sh start    - Start the Dev MCP server
#   ./setup-dev-mcp.sh stop     - Stop the Dev MCP server
#   ./setup-dev-mcp.sh restart  - Restart the Dev MCP server
#   ./setup-dev-mcp.sh status   - Check server status
#   ./setup-dev-mcp.sh logs     - View server logs
#   ./setup-dev-mcp.sh update   - Pull latest image and restart
#===============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/dev-mcp-compose.yml"
CONTAINER_NAME="projexlight-dev-mcp"
IMAGE_NAME="projexlight/projex-dev-mcp:latest"
DEFAULT_PORT=8766
HEALTH_CHECK_RETRIES=30
HEALTH_CHECK_INTERVAL=2

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_msg() {
    local color=$1
    local msg=$2
    echo -e "${color}${msg}${NC}"
}

check_prerequisites() {
    if ! command -v docker &> /dev/null; then
        print_msg "$RED" "[ERROR] Docker is not installed"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        print_msg "$RED" "[ERROR] Docker daemon is not running"
        exit 1
    fi

    if [ ! -f "$COMPOSE_FILE" ]; then
        print_msg "$RED" "[ERROR] Compose file not found: $COMPOSE_FILE"
        exit 1
    fi

    # Detect Docker Compose command (prefer V2 over V1 for compatibility)
    if docker compose version > /dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    elif docker-compose version > /dev/null 2>&1; then
        COMPOSE_CMD="docker-compose"
    else
        print_msg "$RED" "[ERROR] Docker Compose is not installed"
        exit 1
    fi

    # Create sample mcp-config.json if it doesn't exist
    if [ ! -f "$SCRIPT_DIR/mcp-config.json" ]; then
        print_msg "$YELLOW" "[WARN] mcp-config.json not found, creating sample config..."
        create_sample_config
    fi

    # Create feedback directory if it doesn't exist
    mkdir -p "$SCRIPT_DIR/feedback" 2>/dev/null || true

    # Load PROJEXLIGHT_API_URL from .env if not already set
    load_api_url_from_env
}

# Load PROJEXLIGHT_API_URL from .env file
load_api_url_from_env() {
    local env_file="$SCRIPT_DIR/.env"

    # Only load if not already set in environment
    if [ -z "${PROJEXLIGHT_API_URL:-}" ] && [ -f "$env_file" ]; then
        local api_url=$(grep -E "^PROJEXLIGHT_API_URL=" "$env_file" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
        if [ -n "$api_url" ]; then
            export PROJEXLIGHT_API_URL="$api_url"
            print_msg "$CYAN" "[INFO] Using API URL from .env: $api_url"
        fi
    fi

    # Default to production if not set
    export PROJEXLIGHT_API_URL="${PROJEXLIGHT_API_URL:-https://api.projexlight.com}"
}

# Check if container is running
container_running() {
    docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"
}

# Check if container exists (running or stopped)
container_exists() {
    docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"
}

# Check if server is healthy via HTTP health check
check_health() {
    curl -sf http://localhost:${MCP_DEV_PORT:-$DEFAULT_PORT}/health > /dev/null 2>&1
}

# Wait for server to become healthy with retries
wait_for_health() {
    local max_retries=${1:-$HEALTH_CHECK_RETRIES}
    local interval=${2:-$HEALTH_CHECK_INTERVAL}

    print_msg "$YELLOW" "Waiting for server to be ready..."

    for i in $(seq 1 $max_retries); do
        if check_health; then
            print_msg "$GREEN" "[OK] Dev MCP Server is healthy on port ${MCP_DEV_PORT:-$DEFAULT_PORT}"
            return 0
        fi
        echo -ne "${CYAN}[INFO]${NC} Health check attempt $i/$max_retries...\r"
        sleep $interval
    done
    echo ""
    print_msg "$YELLOW" "[WARN] Server may still be starting (health check timed out)"
    return 1
}

# Get the image ID of the running container
get_running_image_id() {
    docker inspect --format='{{.Image}}' "$CONTAINER_NAME" 2>/dev/null | cut -c8-19
}

# Get the image ID of the latest pulled image
get_latest_image_id() {
    docker images --format='{{.ID}}' "$IMAGE_NAME" 2>/dev/null | head -1
}

# Check if container is running with latest image
is_running_latest_image() {
    if ! container_running; then
        return 1
    fi

    local running_id=$(get_running_image_id)
    local latest_id=$(get_latest_image_id)

    if [ -z "$running_id" ] || [ -z "$latest_id" ]; then
        return 1
    fi

    [ "$running_id" = "$latest_id" ]
}

# Pull the latest image
pull_latest_image() {
    print_msg "$BLUE" "Pulling latest image: $IMAGE_NAME..."
    docker pull "$IMAGE_NAME" 2>&1 | grep -E '(Pull|Digest|Status|Downloaded)' || true
}

# Remove container and optionally old images
remove_container() {
    if container_exists; then
        print_msg "$YELLOW" "Removing existing container..."
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    fi
}

# Sync credentials from mcp-config.json to registered project
sync_credentials_if_needed() {
    local port=${MCP_DEV_PORT:-$DEFAULT_PORT}

    # Check if MCP is healthy first
    if ! check_health; then
        return 0  # MCP not ready, skip sync
    fi

    # Get API key from mcp-config.json
    local config_api_key=""
    if [ -f "$SCRIPT_DIR/mcp-config.json" ]; then
        if command -v jq &> /dev/null; then
            config_api_key=$(jq -r '.encryptedPlatformApiKey // .sessionToken // ""' "$SCRIPT_DIR/mcp-config.json" 2>/dev/null)
        fi
    fi

    if [ -z "$config_api_key" ]; then
        return 0  # No API key in config
    fi

    # Get project ID from config
    local project_id=""
    if command -v jq &> /dev/null; then
        project_id=$(jq -r '.projectId // ""' "$SCRIPT_DIR/mcp-config.json" 2>/dev/null)
    fi

    if [ -z "$project_id" ]; then
        return 0  # No project ID
    fi

    # Get registered project's API key
    local registered_api_key=""
    local projects_response
    projects_response=$(curl -sf "http://localhost:${port}/api/projects" 2>/dev/null) || return 0

    if command -v jq &> /dev/null; then
        registered_api_key=$(echo "$projects_response" | jq -r --arg pid "$project_id" '.projects[] | select(.projectId == $pid) | .apiKey // ""' 2>/dev/null)
    fi

    if [ -z "$registered_api_key" ]; then
        return 0  # Project not registered
    fi

    # Compare first 50 chars
    local config_prefix="${config_api_key:0:50}"
    local registered_prefix="${registered_api_key:0:50}"

    if [ "$config_prefix" != "$registered_prefix" ]; then
        print_msg "$YELLOW" "[SYNC] API key mismatch detected - auto-fixing..."

        # Unregister old project
        curl -sf -X DELETE "http://localhost:${port}/api/projects/${project_id}" > /dev/null 2>&1 || true
        sleep 1

        # Re-register with new credentials
        local db_name=$(jq -r '.databaseConfig.database // ""' "$SCRIPT_DIR/mcp-config.json" 2>/dev/null)
        local db_type=$(jq -r '.databaseConfig.type // ""' "$SCRIPT_DIR/mcp-config.json" 2>/dev/null)
        local sprint_id=$(jq -r '.sprintId // ""' "$SCRIPT_DIR/mcp-config.json" 2>/dev/null)
        local db_config=$(jq -c '.databaseConfig // {}' "$SCRIPT_DIR/mcp-config.json" 2>/dev/null)
        local project_name=$(basename "$(dirname "$SCRIPT_DIR")")
        local unix_path=$(cd "$(dirname "$SCRIPT_DIR")" && pwd)

        # Convert Windows path if needed
        if [[ "$unix_path" =~ ^([A-Za-z]):(.*)$ ]]; then
            local drive="${BASH_REMATCH[1]}"
            local rest="${BASH_REMATCH[2]}"
            rest="${rest//\\//}"
            unix_path="/${drive,,}${rest}"
        fi

        curl -sf -X POST "http://localhost:${port}/api/projects/register" \
            -H "Content-Type: application/json" \
            -d "{
                \"projectId\": \"$project_id\",
                \"projectName\": \"$project_name\",
                \"projectPath\": \"$unix_path\",
                \"workspacePath\": \"$unix_path\",
                \"databaseName\": \"$db_name\",
                \"databaseType\": \"$db_type\",
                \"apiKey\": \"$config_api_key\",
                \"sprintId\": \"$sprint_id\",
                \"databaseConfig\": $db_config,
                \"isOwner\": false
            }" > /dev/null 2>&1 || true

        print_msg "$GREEN" "[SYNC] Credentials synced successfully!"
    fi
}

create_sample_config() {
    cat > "$SCRIPT_DIR/mcp-config.json" << 'EOF'
{
  "projectId": "dev-project",
  "sessionToken": "dev-session-token",
  "apiUrl": "http://host.docker.internal:9999",
  "encryptedApiKey": "",
  "databaseConfig": {
    "enabled": true,
    "type": "postgresql",
    "host": "host.docker.internal",
    "port": 5432,
    "database": "appdb",
    "username": "appuser",
    "password": "apppassword"
  },
  "frameworkConfig": {
    "frontend": {
      "framework": "react",
      "language": "typescript",
      "styling": "tailwindcss"
    },
    "backend": {
      "framework": "express",
      "language": "typescript"
    }
  }
}
EOF
    print_msg "$GREEN" "[OK] Created sample mcp-config.json"
    print_msg "$YELLOW" "[INFO] Edit mcp-config.json to customize for your project"
}

start_server() {
    local skip_image_check=${SKIP_IMAGE_CHECK:-false}

    # Check if already running and healthy with latest image
    if container_running && check_health; then
        if [ "$skip_image_check" = "true" ] || is_running_latest_image; then
            print_msg "$GREEN" "[OK] Dev MCP Server is already running and healthy on port ${MCP_DEV_PORT:-$DEFAULT_PORT}"
            return 0
        else
            print_msg "$YELLOW" "[INFO] Container running but not using latest image. Updating..."
        fi
    fi

    cd "$SCRIPT_DIR"

    # Pull latest image first (unless skipped)
    if [ "$skip_image_check" != "true" ]; then
        pull_latest_image
    fi

    # If container exists but not healthy or not latest image, remove it
    if container_exists; then
        if ! check_health || ! is_running_latest_image; then
            print_msg "$YELLOW" "[INFO] Removing old container..."
            remove_container
        fi
    fi

    # Start fresh container
    print_msg "$BLUE" "Starting Dev MCP Server..."
    print_msg "$CYAN" "[INFO] API URL: ${PROJEXLIGHT_API_URL:-https://api.projexlight.com}"
    $COMPOSE_CMD -f dev-mcp-compose.yml up -d

    # Wait for health with retries
    if wait_for_health; then
        # Auto-sync credentials if mcp-config.json has changed
        sync_credentials_if_needed
    else
        print_msg "$YELLOW" "[WARN] Server started but health check timed out - may still be initializing"
    fi
    # Always return 0 - container is started, health check timeout is just a warning
    return 0
}

stop_server() {
    print_msg "$BLUE" "Stopping Dev MCP Server..."

    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f dev-mcp-compose.yml down 2>/dev/null || true

    # Also force remove container if still exists
    if container_exists; then
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    fi

    print_msg "$GREEN" "[OK] Dev MCP Server stopped"
}

restart_server() {
    stop_server
    # Skip image check on restart to avoid double pull
    SKIP_IMAGE_CHECK=true start_server
}

show_status() {
    print_msg "$BLUE" "Dev MCP Server Status"
    echo ""

    if container_running; then
        print_msg "$GREEN" "[RUNNING] Container: $CONTAINER_NAME"

        # Show container details
        docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
        echo ""

        # Show image info
        local running_image=$(docker inspect --format='{{.Config.Image}}' "$CONTAINER_NAME" 2>/dev/null)
        print_msg "$BLUE" "[IMAGE] $running_image"

        # Health check
        if check_health; then
            print_msg "$GREEN" "[HEALTHY] Server responding on port ${MCP_DEV_PORT:-$DEFAULT_PORT}"
        else
            print_msg "$YELLOW" "[WARN] Server not responding to health check"
        fi
    else
        print_msg "$RED" "[STOPPED] Container is not running"
    fi
}

show_logs() {
    print_msg "$BLUE" "Dev MCP Server Logs (Ctrl+C to exit)"
    echo ""

    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f dev-mcp-compose.yml logs -f
}

update_server() {
    print_msg "$BLUE" "Updating Dev MCP Server..."

    cd "$SCRIPT_DIR"

    # Pull latest image
    pull_latest_image

    # Stop and remove old container
    stop_server

    # Start with new image
    SKIP_IMAGE_CHECK=true start_server

    print_msg "$GREEN" "[OK] Dev MCP Server updated"
}

sync_credentials() {
    print_msg "$BLUE" "Syncing credentials from mcp-config.json..."

    if ! check_health; then
        print_msg "$RED" "[ERROR] Dev MCP Server is not running or not healthy"
        print_msg "$YELLOW" "[INFO] Start the server first: $0 start"
        exit 1
    fi

    sync_credentials_if_needed

    print_msg "$GREEN" "[OK] Credential sync complete"
}

show_usage() {
    echo ""
    echo "ProjexLight Dev MCP Server Management"
    echo "======================================"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start    - Start the Dev MCP server (pulls latest image if needed)"
    echo "  stop     - Stop the Dev MCP server"
    echo "  restart  - Restart the Dev MCP server"
    echo "  status   - Check server status"
    echo "  logs     - View server logs (follow mode)"
    echo "  update   - Force pull latest image and restart"
    echo "  sync     - Sync credentials from mcp-config.json (fix 401 errors)"
    echo ""
    echo "Environment Variables:"
    echo "  MCP_DEV_PORT         - Server port (default: 8766)"
    echo "  PROJEXLIGHT_API_URL  - API server URL (default: https://api.projexlight.com)"
    echo "  SKIP_IMAGE_CHECK     - Skip image version check (default: false)"
    echo ""
    echo "Examples:"
    echo "  $0 start"
    echo "  $0 update"
    echo "  MCP_DEV_PORT=9766 $0 start"
    echo ""
}

# Main
check_prerequisites

case "${1:-}" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        restart_server
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    update)
        update_server
        ;;
    sync)
        sync_credentials
        ;;
    *)
        show_usage
        exit 1
        ;;
esac

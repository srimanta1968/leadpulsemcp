#!/bin/bash
#===============================================================================
# ProjexLight TEST MCP Server Setup Script
#===============================================================================
# This script manages the Test/UI MCP Server Docker container.
# The TEST MCP provides UI testing, test execution, and result analysis.
#
# Usage:
#   ./setup-test-mcp.sh start    - Start the Test MCP server
#   ./setup-test-mcp.sh stop     - Stop the Test MCP server
#   ./setup-test-mcp.sh restart  - Restart the Test MCP server
#   ./setup-test-mcp.sh status   - Check server status
#   ./setup-test-mcp.sh logs     - View server logs
#   ./setup-test-mcp.sh update   - Pull latest image and restart
#===============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/test-mcp-compose.yml"
CONTAINER_NAME="projexlight-test-mcp"
IMAGE_NAME="projexlight/projex-test-mcp:latest"
DEFAULT_PORT=8000
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
    curl -sf http://localhost:${MCP_TEST_PORT:-$DEFAULT_PORT}/health > /dev/null 2>&1
}

# Wait for server to become healthy with retries
wait_for_health() {
    local max_retries=${1:-$HEALTH_CHECK_RETRIES}
    local interval=${2:-$HEALTH_CHECK_INTERVAL}

    print_msg "$YELLOW" "Waiting for server to be ready..."

    for i in $(seq 1 $max_retries); do
        if check_health; then
            print_msg "$GREEN" "[OK] Test MCP Server is healthy on port ${MCP_TEST_PORT:-$DEFAULT_PORT}"
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

# Remove container
remove_container() {
    if container_exists; then
        print_msg "$YELLOW" "Removing existing container..."
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    fi
}

create_sample_config() {
    cat > "$SCRIPT_DIR/mcp-config.json" << 'EOF'
{
  "projectId": "test-project",
  "sessionToken": "test-session-token",
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

# Detect frontend port from project configuration
detect_frontend_port() {
    local project_dir="$1"
    local detected_port="3000"  # Default fallback

    # Check for Vite (React/Vue with Vite)
    if [ -f "$project_dir/vite.config.ts" ] || [ -f "$project_dir/vite.config.js" ]; then
        # Check for custom port in vite config
        local vite_port=$(grep -oP "port:\s*\K\d+" "$project_dir/vite.config.ts" "$project_dir/vite.config.js" 2>/dev/null | head -1)
        if [ -n "$vite_port" ]; then
            detected_port="$vite_port"
        else
            detected_port="5173"  # Vite default
        fi
    # Check for Next.js
    elif [ -f "$project_dir/next.config.js" ] || [ -f "$project_dir/next.config.mjs" ]; then
        detected_port="3000"  # Next.js default
    # Check for Angular
    elif [ -f "$project_dir/angular.json" ]; then
        local ng_port=$(jq -r '.projects[].architect.serve.options.port // empty' "$project_dir/angular.json" 2>/dev/null | head -1)
        detected_port="${ng_port:-4200}"  # Angular default
    # Check for Vue CLI
    elif [ -f "$project_dir/vue.config.js" ]; then
        detected_port="8080"  # Vue CLI default
    # Check package.json for hints
    elif [ -f "$project_dir/package.json" ]; then
        # Check if using Vite in scripts
        if grep -q '"vite"' "$project_dir/package.json" 2>/dev/null; then
            detected_port="5173"
        # Check for PORT in scripts
        elif grep -qE 'PORT=\d+' "$project_dir/package.json" 2>/dev/null; then
            local pkg_port=$(grep -oP 'PORT=\K\d+' "$project_dir/package.json" 2>/dev/null | head -1)
            detected_port="${pkg_port:-3000}"
        fi
    fi

    # Check .env files for PORT override
    for env_file in "$project_dir/.env" "$project_dir/.env.local" "$project_dir/.env.development"; do
        if [ -f "$env_file" ]; then
            local env_port=$(grep -oP '^(VITE_)?PORT=\K\d+' "$env_file" 2>/dev/null | head -1)
            if [ -n "$env_port" ]; then
                detected_port="$env_port"
                break
            fi
        fi
    done

    echo "$detected_port"
}

# Detect backend/API port from project configuration
detect_api_port() {
    local project_dir="$1"
    local detected_port="5000"  # Default fallback

    # Check for server directory
    local server_dir=""
    for dir in "$project_dir/server" "$project_dir/api" "$project_dir/backend"; do
        if [ -d "$dir" ]; then
            server_dir="$dir"
            break
        fi
    done

    if [ -n "$server_dir" ]; then
        # Check .env in server directory
        for env_file in "$server_dir/.env" "$server_dir/.env.local"; do
            if [ -f "$env_file" ]; then
                local env_port=$(grep -oP '^PORT=\K\d+' "$env_file" 2>/dev/null | head -1)
                if [ -n "$env_port" ]; then
                    detected_port="$env_port"
                    break
                fi
            fi
        done

        # Check package.json in server directory
        if [ -f "$server_dir/package.json" ]; then
            local pkg_port=$(grep -oP 'PORT[=:]\s*\K\d+' "$server_dir/package.json" 2>/dev/null | head -1)
            if [ -n "$pkg_port" ]; then
                detected_port="$pkg_port"
            fi
        fi
    fi

    # Check root .env for API_PORT or BACKEND_PORT
    if [ -f "$project_dir/.env" ]; then
        local api_port=$(grep -oP '^(API_PORT|BACKEND_PORT|SERVER_PORT)=\K\d+' "$project_dir/.env" 2>/dev/null | head -1)
        if [ -n "$api_port" ]; then
            detected_port="$api_port"
        fi
    fi

    echo "$detected_port"
}

# Create test-config.json in project's tests/config directory
create_test_config() {
    local project_dir="$1"
    local config_dir="$project_dir/tests/config"
    local config_file="$config_dir/test-config.json"
    local template_file="$SCRIPT_DIR/templates/test-config.json"

    # Detect ports from project configuration
    local frontend_port=$(detect_frontend_port "$project_dir")
    local api_port=$(detect_api_port "$project_dir")
    print_msg "$BLUE" "[INFO] Detected frontend port: $frontend_port, API port: $api_port"

    # Create tests/config directory if needed
    if [ ! -d "$config_dir" ]; then
        mkdir -p "$config_dir"
        print_msg "$GREEN" "[OK] Created tests/config directory"
    fi

    # Create test-config.json if it doesn't exist
    if [ ! -f "$config_file" ]; then
        if [ -f "$template_file" ]; then
            cp "$template_file" "$config_file"
            print_msg "$GREEN" "[OK] Created test-config.json from template"
        else
            # Create inline if template not found (using detected ports)
            cat > "$config_file" << TESTCONFIGEOF
{
  "_comment": "=== TEST CONFIGURATION FILE ===",
  "_instructions": [
    "IMPORTANT: Update values below to match your application!",
    "1. testCredentials: Set valid email/password for your app's users",
    "2. loginConfig: Update field names to match your login form",
    "3. environments: Update ports if your app runs on different ports"
  ],
  "\$schema": "https://json-schema.org/draft/2020-12/schema",
  "version": "1.0.0",
  "description": "Test configuration for UI and API testing with projex_test_mcp",

  "environments": {
    "development": {
      "baseUrl": "http://localhost:${frontend_port}",
      "apiUrl": "http://localhost:${api_port}"
    }
  },
  "activeEnvironment": "development",

  "browser": {
    "headless": true,
    "timeout": 30000
  },

  "testCredentials": {
    "_comment": "UPDATE THESE VALUES with valid credentials for your application!",
    "default": {
      "email": "test.user@example.com",
      "password": "Test@12345",
      "_note": "Fallback credentials used when no role specified"
    },
    "admin": {
      "email": "admin@example.com",
      "password": "Admin@12345",
      "_note": "Used with @login:admin tag or 'as admin' in steps"
    },
    "user": {
      "email": "user@example.com",
      "password": "User@12345",
      "_note": "Used with @login:user tag or 'as user' in steps"
    },
    "manager": {
      "email": "manager@example.com",
      "password": "Manager@12345",
      "_note": "Used with @login:manager tag"
    },
    "registered": null
  },

  "loginConfig": {
    "_comment": "UPDATE THESE VALUES to match your login page structure!",
    "loginUrl": "/login",
    "emailField": "email",
    "passwordField": "password",
    "submitButton": "Login",
    "successIndicator": "Dashboard",
    "_fieldNotes": {
      "loginUrl": "Path to your login page (relative or absolute)",
      "emailField": "Name/id/placeholder of email input field",
      "passwordField": "Name/id/placeholder of password input field",
      "submitButton": "Text on your login button",
      "successIndicator": "Text visible after successful login (e.g., 'Dashboard', 'Welcome')"
    }
  },

  "featureValidation": {
    "enabled": true,
    "autoRegenerate": true,
    "syncToDatabase": true,
    "retryFailedTests": true,
    "maxRetries": 3
  },

  "projexlight": {
    "syncEnabled": true
  }
}
TESTCONFIGEOF
            print_msg "$GREEN" "[OK] Created test-config.json with detected ports"
        fi

        # Update the config file with detected ports (works for both template and inline)
        if command -v jq &> /dev/null; then
            # Use jq to update ports properly
            local tmp_file=$(mktemp)
            jq --arg fp "$frontend_port" --arg ap "$api_port" \
                '.environments.development.baseUrl = "http://localhost:" + $fp |
                 .environments.development.apiUrl = "http://localhost:" + $ap' \
                "$config_file" > "$tmp_file" && mv "$tmp_file" "$config_file"
            print_msg "$GREEN" "[OK] Updated config with detected ports (frontend: $frontend_port, API: $api_port)"
        else
            # Fallback: use sed for simple replacement
            sed -i "s|localhost:3000|localhost:${frontend_port}|g" "$config_file" 2>/dev/null || true
            sed -i "s|localhost:5000|localhost:${api_port}|g" "$config_file" 2>/dev/null || true
        fi

        print_msg "$YELLOW" "[INFO] Edit tests/config/test-config.json to set your default test credentials"
    else
        # Check if testCredentials section exists, add if missing
        if ! grep -q '"testCredentials"' "$config_file" 2>/dev/null; then
            print_msg "$YELLOW" "[WARN] test-config.json exists but missing testCredentials section"
            print_msg "$YELLOW" "[INFO] Consider adding testCredentials for login/registration flow support"
        fi
    fi
}

# Detect project directory (parent of mcp-server or current directory)
detect_project_dir() {
    local project_dir

    # Check if we're in mcp-server subdirectory
    if [ "$(basename "$SCRIPT_DIR")" = "mcp-server" ]; then
        project_dir="$(dirname "$SCRIPT_DIR")"
    else
        # Assume script dir parent is project root
        project_dir="$(dirname "$SCRIPT_DIR")"
    fi

    # Validate - check for package.json or tests directory
    if [ -f "$project_dir/package.json" ] || [ -d "$project_dir/tests" ] || [ -d "$project_dir/src" ]; then
        echo "$project_dir"
    else
        # Fallback to script dir parent
        echo "$(dirname "$SCRIPT_DIR")"
    fi
}

start_server() {
    local skip_image_check=${SKIP_IMAGE_CHECK:-false}

    # Detect project directory and create test config if needed
    local project_dir=$(detect_project_dir)
    print_msg "$BLUE" "[INFO] Project directory: $project_dir"
    create_test_config "$project_dir"

    # Check if already running and healthy with latest image
    if container_running && check_health; then
        if [ "$skip_image_check" = "true" ] || is_running_latest_image; then
            print_msg "$GREEN" "[OK] Test MCP Server is already running and healthy on port ${MCP_TEST_PORT:-$DEFAULT_PORT}"
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
    print_msg "$BLUE" "Starting Test MCP Server..."
    print_msg "$CYAN" "[INFO] API URL: ${PROJEXLIGHT_API_URL:-https://api.projexlight.com}"
    $COMPOSE_CMD -f test-mcp-compose.yml up -d

    # Wait for health with retries
    if wait_for_health; then
        :  # Health check passed
    else
        print_msg "$YELLOW" "[WARN] Server started but health check timed out - may still be initializing"
    fi
    # Always return 0 - container is started, health check timeout is just a warning
    return 0
}

stop_server() {
    print_msg "$BLUE" "Stopping Test MCP Server..."

    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f test-mcp-compose.yml down 2>/dev/null || true

    # Also force remove container if still exists
    if container_exists; then
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    fi

    print_msg "$GREEN" "[OK] Test MCP Server stopped"
}

restart_server() {
    stop_server
    # Skip image check on restart to avoid double pull
    SKIP_IMAGE_CHECK=true start_server
}

show_status() {
    print_msg "$BLUE" "Test MCP Server Status"
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
            print_msg "$GREEN" "[HEALTHY] Server responding on port ${MCP_TEST_PORT:-$DEFAULT_PORT}"
        else
            print_msg "$YELLOW" "[WARN] Server not responding to health check"
        fi
    else
        print_msg "$RED" "[STOPPED] Container is not running"
    fi
}

show_logs() {
    print_msg "$BLUE" "Test MCP Server Logs (Ctrl+C to exit)"
    echo ""

    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f test-mcp-compose.yml logs -f
}

update_server() {
    print_msg "$BLUE" "Updating Test MCP Server..."

    cd "$SCRIPT_DIR"

    # Pull latest image
    pull_latest_image

    # Stop and remove old container
    stop_server

    # Start with new image
    SKIP_IMAGE_CHECK=true start_server

    print_msg "$GREEN" "[OK] Test MCP Server updated"
}

show_usage() {
    echo ""
    echo "ProjexLight Test MCP Server Management"
    echo "======================================="
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start    - Start the Test MCP server (pulls latest image if needed)"
    echo "  stop     - Stop the Test MCP server"
    echo "  restart  - Restart the Test MCP server"
    echo "  status   - Check server status"
    echo "  logs     - View server logs (follow mode)"
    echo "  update   - Force pull latest image and restart"
    echo ""
    echo "Environment Variables:"
    echo "  MCP_TEST_PORT        - Server port (default: 8000)"
    echo "  PROJEXLIGHT_API_URL  - API server URL (default: https://api.projexlight.com)"
    echo "  SKIP_IMAGE_CHECK     - Skip image version check (default: false)"
    echo ""
    echo "Examples:"
    echo "  $0 start"
    echo "  $0 update"
    echo "  MCP_TEST_PORT=9000 $0 start"
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
    *)
        show_usage
        exit 1
        ;;
esac

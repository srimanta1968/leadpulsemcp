#!/bin/bash
#===============================================================================
# ProjexLight Test Runner
#===============================================================================
# Runs UI and API tests using the Test MCP Docker container
#
# Usage:
#   ./run-all-tests.sh ui [feature_file]     - Run UI tests (from local feature files)
#   ./run-all-tests.sh api                   - Run API tests (from api_library database)
#   ./run-all-tests.sh unified               - Run UI + API tests together
#   ./run-all-tests.sh all                   - Run all tests
#   ./run-all-tests.sh status                - Check test MCP status
#
# API Tests:
#   API tests ALWAYS fetch definitions from ProjexLight api_library table.
#   The api_library is populated by Dev MCP via pre-commit/pre-push hooks.
#   No --mode option needed - database mode is always used.
#
# UI Tests:
#   UI tests run from local tests/features/*.feature files.
#   --mode database option available for unified command only.
#
# Environment:
#   --env <name>      - Use specific environment from test-config.json
#                       (e.g., development, staging, production, qa, regression)
#                       URLs are read from environments.<name>.baseUrl and apiUrl
#
# Test-user credentials (written into tests/config/test-config.json before run):
#   --login-email <email>     Override testCredentials.default.email. Useful
#                             when a test flow actually sends mail (password
#                             reset, email verification) and you need a real
#                             deliverable mailbox. Without this flag the
#                             existing config default is used.
#   --login-password <pass>   Optional password to pair with --login-email.
#                             If omitted, the existing config password is
#                             preserved; if config has none, a default is
#                             generated.
#   --admin-email <email>     Override testCredentials.admin.email. Used by
#                             Test MCP when an api_definition declares
#                             requiresRole: ['admin'] — the admin user must
#                             exist in DB with role='admin'.
#   --admin-password <pass>   Optional password to pair with --admin-email.
#                             Same precedence rules as --login-password.
#
# How auth works (behind the scenes, no manual steps needed):
#   * Dev MCP reads testCredentials.default from test-config.json on every
#     test run.
#   * If the user exists in the DB with that password → login, skip register.
#   * If the user doesn't exist → register with those creds. Persisted to
#     tests/config/.auth-session.json for faster future runs.
#   * If the user exists but the password is wrong (e.g. the app returns
#     201-with-success:false on duplicate) → test-only DB password reset
#     with a fresh bcrypt hash. Plaintext is logged in a banner so you can
#     see what was set.
#   * Safety rail: DB reset is ONLY applied to emails that look like test
#     users (contain 'test', example.com, mailinator, or match the configured
#     default). Production-looking emails are never touched.
#
# Examples:
#   ./run-all-tests.sh ui                                    # All UI tests
#   ./run-all-tests.sh ui lead-contact-management.feature   # Single feature
#   ./run-all-tests.sh api                                   # All API tests
#   ./run-all-tests.sh api --env staging                    # API tests against staging
#   ./run-all-tests.sh api --feature-id abc123              # API tests for specific feature
#   ./run-all-tests.sh unified                              # UI + API tests
#   ./run-all-tests.sh unified --env production             # UI + API against production
#
# Environment Variables (set in mcp-server/.env file):
#   PROJEXLIGHT_API_URL    - ProjexLight API URL (required for API tests)
#   PROJEXLIGHT_API_KEY    - Tenant API key in format 'ak_...' (required for API tests)
#                            Create in ProjexLight: Settings > API Keys > Create Key
#   PROJEXLIGHT_PROJECT_ID - Project ID (falls back to mcp-config.json projectId)
#   FEATURE_ID             - Feature ID to test (optional)
#   SPRINT_ID              - Sprint ID to test (optional)
#===============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Exit codes
HOST_NOT_ACCESSIBLE_EXIT_CODE=78
INVALID_NAVIGATION_EXIT_CODE=79

# Configuration
TEST_MCP_IMAGE="${TEST_MCP_IMAGE:-projexlight/projex-test-mcp:latest}"
TEST_MCP_CONTAINER="${TEST_MCP_CONTAINER:-projexlight-test-mcp}"
TESTS_DIR="${PROJECT_ROOT}/tests"
RESULTS_DIR="${PROJECT_ROOT}/test-results"
CONFIG_FILE="${TESTS_DIR}/config/test-config.json"

# Track if config was created during this run (for failure hints)
CONFIG_CREATED_THIS_RUN=false

# Create test-config.json if it doesn't exist
ensure_test_config() {
    local config_dir="${TESTS_DIR}/config"
    local template_file="${SCRIPT_DIR}/templates/test-config.json"

    # Skip if config already exists
    if [ -f "$CONFIG_FILE" ]; then
        return 0
    fi

    # Create tests/config directory if needed
    if [ ! -d "$config_dir" ]; then
        mkdir -p "$config_dir"
        print_success "Created tests/config directory"
    fi

    # Create test-config.json from template or inline
    if [ -f "$template_file" ]; then
        cp "$template_file" "$CONFIG_FILE"
        print_success "Created test-config.json from template"
    else
        # Create minimal config inline
        cat > "$CONFIG_FILE" << 'TESTCONFIGEOF'
{
  "version": "1.0.0",
  "environments": {
    "development": {
      "baseUrl": "http://localhost:3000",
      "apiUrl": "http://localhost:5000"
    }
  },
  "activeEnvironment": "development",
  "browser": {
    "headless": true,
    "timeout": 30000
  },
  "testCredentials": {
    "default": {
      "email": "default_test_user@example.com",
      "password": "DefaultTestPass123!",
      "description": "Fallback creds (role: user)"
    },
    "admin": {
      "email": "REPLACE_ME_admin@example.com",
      "password": "REPLACE_ME_AdminPass123!",
      "description": "Admin user creds. Test MCP uses these when an api_definition declares requiresRole: ['admin']. The user must exist in DB with role='admin'. If your app doesn't allow self-registration as admin, either pre-seed in init scripts OR enable test-only DB role promotion. Override via --admin-email / --admin-password flags."
    },
    "registered": null
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
        print_success "Created default test-config.json"
    fi

    # Mark that we created config this run
    CONFIG_CREATED_THIS_RUN=true
    print_warning "New config created - please verify settings before running tests"
    print_warning "Config file: $CONFIG_FILE"
}

# Apply --login-email / --login-password / --admin-email / --admin-password
# overrides to testCredentials.{default,admin}. Called after ensure_test_config
# so the file always exists. Uses jq for atomic rewrite. Preserves existing
# password if only the matching email flag is passed.
#
# The admin slot is what Test MCP reads when an api_definition declares
# requiresRole: ['admin']. See api_test_runner.py _get_or_fetch_role_token.
apply_login_overrides() {
    if [ -z "${LOGIN_EMAIL_OVERRIDE:-}" ] && [ -z "${LOGIN_PASSWORD_OVERRIDE:-}" ] && \
       [ -z "${ADMIN_EMAIL_OVERRIDE:-}" ] && [ -z "${ADMIN_PASSWORD_OVERRIDE:-}" ]; then
        return 0
    fi

    if ! command -v jq >/dev/null 2>&1; then
        print_error "jq is required for --login-email / --login-password / --admin-email / --admin-password. Install jq and retry."
        exit 1
    fi

    local session_cleared=false

    # --- default (regular user) slot -----------------------------------------
    if [ -n "${LOGIN_EMAIL_OVERRIDE:-}" ] || [ -n "${LOGIN_PASSWORD_OVERRIDE:-}" ]; then
        local current_email current_password new_email new_password tmp_file
        current_email=$(jq -r '.testCredentials.default.email // ""' "$CONFIG_FILE" 2>/dev/null)
        current_password=$(jq -r '.testCredentials.default.password // ""' "$CONFIG_FILE" 2>/dev/null)

        new_email="${LOGIN_EMAIL_OVERRIDE:-$current_email}"
        if [ -n "${LOGIN_PASSWORD_OVERRIDE:-}" ]; then
            new_password="$LOGIN_PASSWORD_OVERRIDE"
        elif [ -n "$current_password" ]; then
            new_password="$current_password"
        else
            new_password="ProjexTest@1234!"
            print_warning "No default password in config and none provided — using generated default"
        fi

        if [ "$new_email" = "$current_email" ] && [ "$new_password" = "$current_password" ]; then
            echo "  Login credentials unchanged (already $current_email)"
        else
            tmp_file="${CONFIG_FILE}.tmp.$$"
            jq --arg email "$new_email" --arg password "$new_password" \
               '.testCredentials = (.testCredentials // {}) |
                .testCredentials.default = (.testCredentials.default // {}) |
                .testCredentials.default.email = $email |
                .testCredentials.default.password = $password' \
               "$CONFIG_FILE" > "$tmp_file"

            if [ -s "$tmp_file" ]; then
                mv "$tmp_file" "$CONFIG_FILE"
                [ "$new_email" != "$current_email" ] && print_success "Login email override → $new_email"
                [ "$new_password" != "$current_password" ] && print_success "Login password updated (source: --login-password flag)"
                session_cleared=true
            else
                rm -f "$tmp_file"
                print_error "Failed to write login overrides to $CONFIG_FILE"
                exit 1
            fi
        fi
    fi

    # --- admin slot ----------------------------------------------------------
    if [ -n "${ADMIN_EMAIL_OVERRIDE:-}" ] || [ -n "${ADMIN_PASSWORD_OVERRIDE:-}" ]; then
        local admin_current_email admin_current_password admin_new_email admin_new_password admin_tmp_file
        admin_current_email=$(jq -r '.testCredentials.admin.email // ""' "$CONFIG_FILE" 2>/dev/null)
        admin_current_password=$(jq -r '.testCredentials.admin.password // ""' "$CONFIG_FILE" 2>/dev/null)

        admin_new_email="${ADMIN_EMAIL_OVERRIDE:-$admin_current_email}"
        if [ -n "${ADMIN_PASSWORD_OVERRIDE:-}" ]; then
            admin_new_password="$ADMIN_PASSWORD_OVERRIDE"
        elif [ -n "$admin_current_password" ]; then
            admin_new_password="$admin_current_password"
        else
            admin_new_password="ProjexAdmin@1234!"
            print_warning "No admin password in config and none provided — using generated default"
        fi

        if [ "$admin_new_email" = "$admin_current_email" ] && [ "$admin_new_password" = "$admin_current_password" ]; then
            echo "  Admin credentials unchanged (already $admin_current_email)"
        else
            admin_tmp_file="${CONFIG_FILE}.tmp.admin.$$"
            jq --arg email "$admin_new_email" --arg password "$admin_new_password" \
               '.testCredentials = (.testCredentials // {}) |
                .testCredentials.admin = (.testCredentials.admin // {"description": "Admin user creds. Test MCP uses these when an api_definition declares requiresRole: [\"admin\"]. The user must exist in DB with role=admin."}) |
                .testCredentials.admin.email = $email |
                .testCredentials.admin.password = $password' \
               "$CONFIG_FILE" > "$admin_tmp_file"

            if [ -s "$admin_tmp_file" ]; then
                mv "$admin_tmp_file" "$CONFIG_FILE"
                [ "$admin_new_email" != "$admin_current_email" ] && print_success "Admin email override → $admin_new_email"
                [ "$admin_new_password" != "$admin_current_password" ] && print_success "Admin password updated (source: --admin-password flag)"
                session_cleared=true
            else
                rm -f "$admin_tmp_file"
                print_error "Failed to write admin overrides to $CONFIG_FILE"
                exit 1
            fi
        fi
    fi

    # Delete stale sidecar once if any creds actually changed. MCP already
    # ignores sidecar when email mismatches, but cleaning it up avoids
    # confusion in logs/git status.
    if [ "$session_cleared" = "true" ] && [ -f "${TESTS_DIR}/config/.auth-session.json" ]; then
        rm -f "${TESTS_DIR}/config/.auth-session.json"
        echo "  Cleared stale .auth-session.json"
    fi
}

# Show hint to check config if tests fail and config was just created
show_config_check_hint() {
    if [ "$CONFIG_CREATED_THIS_RUN" = "true" ]; then
        echo ""
        print_warning "=================================================="
        print_warning "  TEST CONFIG WAS JUST CREATED - PLEASE VERIFY"
        print_warning "=================================================="
        echo ""
        echo "  A new test-config.json was created with default values."
        echo "  If tests failed, please check the following settings:"
        echo ""
        echo "  1. Environment URLs:"
        echo "     - baseUrl: Your application's frontend URL"
        echo "     - apiUrl: Your application's API URL"
        echo ""
        echo "  2. Test Credentials (for login/registration tests):"
        echo "     - default.email: A valid test user email"
        echo "     - default.password: The test user's password"
        echo ""
        echo "  Config file location:"
        echo "     $CONFIG_FILE"
        echo ""
        print_warning "Edit the config and re-run tests after verification"
        echo ""
    fi
}

# Load test configuration from JSON file
# Uses ENV_OVERRIDE if set (from --env argument), otherwise reads activeEnvironment from config
load_test_config() {
    # Ensure config file exists first
    ensure_test_config

    if [ -f "$CONFIG_FILE" ]; then
        echo "  Loading test config from: $CONFIG_FILE"

        # Get active environment (command line --env overrides config file)
        local active_env
        if [ -n "${ENV_OVERRIDE:-}" ]; then
            active_env="$ENV_OVERRIDE"
            echo "  Environment (from --env): $active_env"
        else
            active_env=$(jq -r '.activeEnvironment // "development"' "$CONFIG_FILE")
            echo "  Environment (from config): $active_env"
        fi

        # Validate environment exists in config
        local env_exists=$(jq -r ".environments.${active_env} // \"null\"" "$CONFIG_FILE")
        if [ "$env_exists" = "null" ]; then
            print_error "Environment '$active_env' not found in test-config.json"
            echo ""
            echo "  Available environments:"
            jq -r '.environments | keys[]' "$CONFIG_FILE" | while read env; do
                local desc=$(jq -r ".environments.${env}.description // \"\"" "$CONFIG_FILE")
                echo "    - $env: $desc"
            done
            echo ""
            echo "  To add a new environment, edit: $CONFIG_FILE"
            echo "  Example:"
            echo "    \"environments\": {"
            echo "      \"$active_env\": {"
            echo "        \"baseUrl\": \"https://your-$active_env-url.com\","
            echo "        \"apiUrl\": \"https://api-$active_env-url.com\","
            echo "        \"description\": \"$active_env environment\""
            echo "      }"
            echo "    }"
            exit 1
        fi

        # Get base URL for the active environment
        local base_url=$(jq -r ".environments.${active_env}.baseUrl // \"http://localhost:3000\"" "$CONFIG_FILE")
        local api_url=$(jq -r ".environments.${active_env}.apiUrl // \"http://localhost:3000\"" "$CONFIG_FILE")

        # Translate localhost to host.docker.internal for Docker container access
        # This allows the Test MCP container to reach services on the host machine
        # Skip translation if:
        #   1. SKIP_LOCALHOST_TRANSLATION=true (for cloud environments like AWS Fargate, Azure, GCP)
        #   2. Running in AWS (detected via AWS_EXECUTION_ENV or ECS_CONTAINER_METADATA_URI)
        #   3. Running in Azure (detected via WEBSITE_INSTANCE_ID or AZURE_CONTAINER_*)
        #   4. Running in GCP (detected via K_SERVICE or GOOGLE_CLOUD_PROJECT)
        local is_cloud_env=false
        if [ "${SKIP_LOCALHOST_TRANSLATION:-false}" = "true" ] || \
           [ -n "${AWS_EXECUTION_ENV:-}" ] || [ -n "${ECS_CONTAINER_METADATA_URI:-}" ] || \
           [ -n "${WEBSITE_INSTANCE_ID:-}" ] || [ -n "${AZURE_CONTAINER_APP_NAME:-}" ] || \
           [ -n "${K_SERVICE:-}" ] || [ -n "${GOOGLE_CLOUD_PROJECT:-}" ]; then
            is_cloud_env=true
            echo "  Cloud environment detected - skipping localhost translation"
        fi

        if [ "$is_cloud_env" = "false" ]; then
            # Only translate if URL contains localhost (local Docker Desktop mode)
            base_url=$(echo "$base_url" | sed 's|localhost|host.docker.internal|g')
            api_url=$(echo "$api_url" | sed 's|localhost|host.docker.internal|g')
        fi

        # Export as environment variables.
        #
        # Precedence rule:
        #   - LOCAL mode (active env = development): test-config.json wins —
        #     because auto-detect-ports.sh just updated it to match the live
        #     running ports. Any stale API_BASE_URL / UI_BASE_URL inherited
        #     from mcp-server/.env (e.g. a hardcoded :3005 from a previous
        #     framework) must NOT override auto-detected reality, otherwise
        #     every test errors with "Cannot connect to host…".
        #   - REMOTE mode (staging/qa/production): the env var override is
        #     respected, because the remote host isn't introspectable and
        #     CI may inject a different URL than the one committed in
        #     test-config.json.
        if [ "$active_env" = "development" ]; then
            # Warn if the dev is about to be rescued from a stale override
            if [ -n "${API_BASE_URL:-}" ] && [ "$API_BASE_URL" != "$api_url" ]; then
                print_warning "Ignoring stale API_BASE_URL=$API_BASE_URL from env — using auto-detected $api_url"
            fi
            if [ -n "${UI_BASE_URL:-}" ] && [ "$UI_BASE_URL" != "$base_url" ]; then
                print_warning "Ignoring stale UI_BASE_URL=$UI_BASE_URL from env — using auto-detected $base_url"
            fi
            export UI_BASE_URL="$base_url"
            export API_BASE_URL="$api_url"
        else
            # Remote env: env-var override wins (CI injection), else config wins
            export UI_BASE_URL="${UI_BASE_URL:-$base_url}"
            export API_BASE_URL="${API_BASE_URL:-$api_url}"
        fi

        # Load browser settings
        export BROWSER_HEADLESS=$(jq -r '.browser.headless // true' "$CONFIG_FILE")
        export BROWSER_TIMEOUT=$(jq -r '.browser.timeout // 30000' "$CONFIG_FILE")

        # Load feature validation settings
        export FEATURE_VALIDATION_ENABLED=$(jq -r '.featureValidation.enabled // true' "$CONFIG_FILE")
        export FEATURE_AUTO_REGENERATE=$(jq -r '.featureValidation.autoRegenerate // true' "$CONFIG_FILE")
        export FEATURE_SYNC_TO_DB=$(jq -r '.featureValidation.syncToDatabase // true' "$CONFIG_FILE")
        export FEATURE_RETRY_FAILED=$(jq -r '.featureValidation.retryFailedTests // true' "$CONFIG_FILE")
        export FEATURE_MAX_RETRIES=$(jq -r '.featureValidation.maxRetries // 3' "$CONFIG_FILE")

        # Load ProjexLight settings
        export PROJEXLIGHT_SYNC_ENABLED=$(jq -r '.projexlight.syncEnabled // true' "$CONFIG_FILE")

        echo "  UI Base URL: $UI_BASE_URL"
        echo "  API Base URL: $API_BASE_URL"
        echo "  Feature validation: $FEATURE_VALIDATION_ENABLED"
    else
        echo "  No config file found at $CONFIG_FILE"
        echo "  Using default configuration"
        export UI_BASE_URL="${UI_BASE_URL:-http://localhost:3000}"
        export API_BASE_URL="${API_BASE_URL:-http://localhost:5000}"
    fi
}

# Validate feature file before running tests
validate_feature_file() {
    local feature_path="$1"
    local feature_name=$(basename "$feature_path")

    if [ "$FEATURE_VALIDATION_ENABLED" != "true" ]; then
        echo "  Feature validation disabled, skipping..."
        return 0
    fi

    echo "  Validating feature file: $feature_name"

    # Read feature content
    local feature_content=$(cat "$feature_path" | jq -Rs .)

    # Get Dev MCP port (default 8766)
    local dev_mcp_port="${MCP_DEV_PORT:-8766}"

    # Call validation endpoint
    local validation_result=$(curl -sf -X POST "http://localhost:${dev_mcp_port}/api/feature/validate" \
        -H "Content-Type: application/json" \
        -d "{
            \"feature_content\": $feature_content,
            \"scenario_context\": \"UI testing\"
        }" 2>/dev/null)

    if [ $? -ne 0 ]; then
        print_warning "Could not validate feature file (Dev MCP not available)"
        return 0
    fi

    local is_valid=$(echo "$validation_result" | jq -r '.validation.is_valid // true')

    if [ "$is_valid" = "false" ]; then
        print_warning "Feature file has issues"

        # Show issues
        echo "$validation_result" | jq -r '.validation.suggestions[]' 2>/dev/null | while read -r suggestion; do
            echo "    - $suggestion"
        done

        # Auto-regenerate if enabled
        if [ "$FEATURE_AUTO_REGENERATE" = "true" ]; then
            echo "  Auto-regenerating feature file..."

            local regen_result=$(curl -sf -X POST "http://localhost:${dev_mcp_port}/api/feature/regenerate" \
                -H "Content-Type: application/json" \
                -d "{
                    \"feature_content\": $feature_content,
                    \"scenario_context\": \"UI testing\",
                    \"sync_to_db\": $FEATURE_SYNC_TO_DB
                }" 2>/dev/null)

            if [ $? -eq 0 ]; then
                local has_changes=$(echo "$regen_result" | jq -r '.has_changes // false')
                if [ "$has_changes" = "true" ]; then
                    # Save regenerated content
                    echo "$regen_result" | jq -r '.regenerated_content' > "$feature_path"
                    print_success "Feature file regenerated with dynamic values"
                fi
            fi
        fi
    else
        print_success "Feature file is valid"
    fi

    return 0
}

# Multi-project support: Get Unix-style project path for path translation
get_unix_project_path() {
    local path="$PROJECT_ROOT"
    # Convert backslashes to forward slashes
    path="${path//\\//}"
    # Convert Windows drive letter (C:/ -> /c/)
    if [[ "$path" =~ ^([A-Za-z]):(.*)$ ]]; then
        local drive="${BASH_REMATCH[1]}"
        local rest="${BASH_REMATCH[2]}"
        path="/${drive,,}${rest}"
    fi
    echo "$path"
}

# Get container workspace path for this project
# Returns /workspace for owner project, /projects/additionalN for others
get_container_workspace() {
    local unix_path=$(get_unix_project_path)

    # Check registered projects to find the container path
    local registered_dir="$SCRIPT_DIR/registered_projects"
    if [ -d "$registered_dir" ]; then
        # Check if this is the owner project
        if [ -f "$registered_dir/owner.env" ]; then
            local owner_path=$(grep "^PROJECT_PATH=" "$registered_dir/owner.env" 2>/dev/null | cut -d'=' -f2)
            if [ "$owner_path" = "$unix_path" ]; then
                echo "/workspace"
                return
            fi
        fi

        # Check additional project slots
        for slot in 1 2 3; do
            local env_file="$registered_dir/additional${slot}.env"
            if [ -f "$env_file" ]; then
                local proj_path=$(grep "^PROJECT_PATH=" "$env_file" 2>/dev/null | cut -d'=' -f2)
                if [ "$proj_path" = "$unix_path" ]; then
                    echo "/projects/additional${slot}"
                    return
                fi
            fi
        done
    fi

    # Default to /workspace if not found
    echo "/workspace"
}

print_header() {
    echo ""
    echo -e "${BLUE}=============================================="
    echo -e "$1"
    echo -e "==============================================${NC}"
}

print_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Show host accessibility guide when client framework blocks Docker requests
show_host_accessibility_guide() {
    echo ""
    echo -e "${RED}======================================================================"
    echo -e "  ❌ CLIENT FRAMEWORK NOT ACCESSIBLE FROM DOCKER"
    echo -e "======================================================================${NC}"
    echo ""
    echo "  The Test MCP container cannot connect to your local development server."
    echo ""
    echo "  This happens because your client framework is blocking requests from"
    echo "  'host.docker.internal' (the hostname Docker uses to reach the host machine)."
    echo ""
    echo -e "${CYAN}======================================================================${NC}"
    echo -e "${CYAN}  📋 HOW TO FIX${NC}"
    echo -e "${CYAN}======================================================================${NC}"
    echo ""
    echo -e "  ${GREEN}Vite Configuration${NC} (vite.config.ts):"
    echo ""
    echo "    export default defineConfig({"
    echo "      server: {"
    echo "        allowedHosts: ['host.docker.internal', 'localhost', '127.0.0.1'],"
    echo "        // your other settings..."
    echo "      }"
    echo "    })"
    echo ""
    echo -e "  ${GREEN}Create React App${NC} (package.json scripts or .env):"
    echo ""
    echo "    DANGEROUSLY_DISABLE_HOST_CHECK=true"
    echo ""
    echo -e "  ${GREEN}Next.js${NC} (next.config.js):"
    echo ""
    echo "    Add 'host.docker.internal' to allowed hosts"
    echo ""
    echo -e "  ${GREEN}Angular${NC} (CLI):"
    echo ""
    echo "    ng serve --disable-host-check"
    echo ""
    echo -e "  ${GREEN}Vue CLI${NC} (vue.config.js):"
    echo ""
    echo "    module.exports = {"
    echo "      devServer: {"
    echo "        disableHostCheck: true"
    echo "      }"
    echo "    }"
    echo ""
    echo -e "${YELLOW}======================================================================${NC}"
    echo -e "${YELLOW}  ⚡ QUICK FIX FOR VITE (most common)${NC}"
    echo -e "${YELLOW}======================================================================${NC}"
    echo ""
    echo "  1. Open your vite.config.ts (or vite.config.js)"
    echo ""
    echo "  2. Add this to the server section:"
    echo ""
    echo "     allowedHosts: ['host.docker.internal', 'localhost']"
    echo ""
    echo "  3. Restart your dev server (npm run dev)"
    echo ""
    echo "  4. Re-run the tests"
    echo ""
    echo -e "${BLUE}======================================================================${NC}"
    echo "  After making the configuration change, restart your dev server and re-run tests."
    echo -e "${BLUE}======================================================================${NC}"
    echo ""
}

# Show invalid navigation step error (extracted from test output)
show_invalid_navigation_error() {
    local step_text="${1:-}"
    local suggested_fix="${2:-}"

    echo ""
    echo -e "${RED}INVALID NAVIGATION STEP - Missing URL path${NC}"
    echo ""
    if [ -n "$step_text" ]; then
        echo "  Your step:    $step_text"
    fi
    if [ -n "$suggested_fix" ]; then
        echo "  Suggested:    $suggested_fix"
    fi
    echo ""
    echo "  Syntax: Given I navigate to <page> at \"<url-path>\""
    echo ""
    echo "  Find the URL path by opening your app in browser and copying the path after the domain."
    echo ""
}

check_prerequisites() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        print_error "Docker daemon is not running"
        exit 1
    fi

    if [ ! -d "$TESTS_DIR" ]; then
        print_error "Tests directory not found: $TESTS_DIR"
        exit 1
    fi

    # Check for jq (required for config parsing)
    if ! command -v jq &> /dev/null; then
        print_warning "jq not installed - config file parsing will be limited"
    fi

    # Auto-detect SUT ports from framework config files and update
    # tests/config/test-config.json if they're stale. This removes the
    # manual "edit baseUrl/apiUrl every time you change frameworks" step.
    #
    # Delegates to auto-detect-ports.sh which parses:
    #   - server/.env, backend/.env, api/.env for PORT= (Node/Express/etc.)
    #   - application.properties (Spring Boot)
    #   - vite.config.ts|js, next.config.js, angular.json, nuxt.config.ts,
    #     svelte.config.js, astro.config.mjs, package.json (CRA)
    #
    # Auto-detect ONLY runs for the 'development' environment, because
    # staging/qa/production targets are REMOTE URLs (https://qa.example.com
    # or a coworker's machine on the LAN) that can't be introspected from
    # this machine's filesystem. For those environments, the values in
    # test-config.json are authoritative — hand-edited by the dev team or
    # set in CI, and the auto-detect would just clobber them.
    #
    # Disable with SUT_AUTO_CONFIG=false in the environment. The detection
    # is soft — if a framework isn't recognized or ports aren't listening,
    # the existing test-config.json values are left alone and the user can
    # override via UI_BASE_URL / API_BASE_URL env vars.
    if [ "${SUT_AUTO_CONFIG:-true}" = "true" ] && \
       [ "${ENV_OVERRIDE:-development}" = "development" ]; then
        local auto_script="$SCRIPT_DIR/auto-detect-ports.sh"
        if [ -x "$auto_script" ]; then
            "$auto_script" "$PROJECT_ROOT" || true
        fi
    elif [ -n "${ENV_OVERRIDE:-}" ] && [ "$ENV_OVERRIDE" != "development" ]; then
        print_warning "Auto-detect skipped — targeting remote environment '$ENV_OVERRIDE'"
        print_warning "  Remote URLs are read as-is from test-config.json. To update them,"
        print_warning "  edit .environments.${ENV_OVERRIDE}.baseUrl / .apiUrl directly."
    fi

    # Load test configuration
    print_header "Loading Test Configuration"
    load_test_config
}

# Pre-flight: verify the SUT (system under test) is reachable from the
# host AND from inside the Test MCP container before running any tests.
#
# This catches the single most common cause of mass-test-failure with
# "Cannot connect to host host.docker.internal:PORT" errors: your web
# app is either not running, or it's bound to 127.0.0.1 (loopback only)
# instead of 0.0.0.0 (all interfaces), which Docker containers cannot
# reach even via host.docker.internal.
#
# We delegate to check-sut.sh (a shared helper), which runs the probe
# and prints framework-specific guidance on failure, pointing users at
# docs/SUT_SETUP_GUIDE.md for the full fix.
#
# Argument: "api" | "ui" | "both"
#   - "api"  → probe only the API URL (API_BASE_URL — from test-config.json
#              apiUrl, points to the backend, typically port 3005).
#              Used by `run-all-tests.sh api`.
#   - "ui"   → probe only the UI URL (UI_BASE_URL — from test-config.json
#              baseUrl, points to the frontend dev server, typically port
#              3000 for CRA/Next/Angular or 5173 for Vite).
#              Used by `run-all-tests.sh ui`.
#   - "both" → probe both. Used by `run-all-tests.sh unified` and `all`.
#
# IMPORTANT: this function MUST read UI_BASE_URL and API_BASE_URL, not
# BASE_URL / API_URL. load_test_config() exports those exact names after
# translating localhost → host.docker.internal. Reading BASE_URL here
# silently falls back to a hardcoded default and would give false
# failures on frameworks whose dev server runs on a non-3000 port.
#
# This mode-aware probing is intentional: running `api` tests does NOT
# require the frontend dev server to be up. Probing port 3000 during an
# API-only run would give a false failure whenever the UI is not
# started, which is a common workflow (e.g. running API tests in a
# terminal while a separate team owns the frontend).
#
# To skip this check entirely (e.g. in CI where the SUT runs elsewhere
# and is already known-healthy), set SKIP_SUT_CHECK=true in the env.
check_sut_connectivity() {
    local mode="${1:-both}"

    if [ "${SKIP_SUT_CHECK:-false}" = "true" ]; then
        print_warning "SUT connectivity pre-flight skipped (SKIP_SUT_CHECK=true)"
        return 0
    fi

    local check_script="$SCRIPT_DIR/check-sut.sh"
    if [ ! -x "$check_script" ]; then
        # check-sut.sh is shipped by CLI export alongside this script.
        # If it's missing, something is wrong with the export — warn but
        # don't block the run (the user might have customized their layout).
        print_warning "check-sut.sh not found at $check_script — skipping SUT pre-flight"
        print_warning "  If you see 'Cannot connect to host.docker.internal:PORT' errors,"
        print_warning "  see docs/SUT_SETUP_GUIDE.md for how to bind your app to 0.0.0.0"
        return 0
    fi

    # Resolve URLs based on mode. load_test_config has already populated
    # UI_BASE_URL (from baseUrl) and API_BASE_URL (from apiUrl) in
    # test-config.json, with localhost already translated to
    # host.docker.internal for the Docker container path.
    local probe_primary=""
    local probe_secondary=""
    case "$mode" in
        api)
            probe_primary="${API_BASE_URL:-http://host.docker.internal:3005}"
            ;;
        ui)
            probe_primary="${UI_BASE_URL:-http://host.docker.internal:3000}"
            ;;
        both|*)
            probe_primary="${UI_BASE_URL:-http://host.docker.internal:3000}"
            probe_secondary="${API_BASE_URL:-}"
            ;;
    esac

    SUT_BASE_URL="$probe_primary" \
    SUT_API_URL="$probe_secondary" \
    SUT_CONTAINER="${TEST_MCP_CONTAINER}" \
        "$check_script"
    local rc=$?

    if [ $rc -ne 0 ]; then
        print_error "SUT pre-flight check failed — aborting test run"
        echo ""
        echo "  Fix your SUT and re-run. To bypass this check (NOT recommended):"
        echo "    SKIP_SUT_CHECK=true ./run-all-tests.sh $COMMAND"
        echo ""
        exit 78   # HOST_NOT_ACCESSIBLE_EXIT_CODE — reuses the existing code
    fi
}

ensure_test_mcp() {
    print_header "Ensuring Test MCP Container"

    # Check if container is running
    if docker ps --format '{{.Names}}' | grep -q "^${TEST_MCP_CONTAINER}$"; then
        print_success "Test MCP container is running"
        return 0
    fi

    # Pull latest image
    print_warning "Pulling Test MCP image..."
    docker pull "$TEST_MCP_IMAGE"

    # Start container
    print_warning "Starting Test MCP container..."
    "$SCRIPT_DIR/setup-test-mcp.sh" start

    # Wait for health check
    sleep 5
    print_success "Test MCP container started"
}

run_ui_tests() {
    local feature_file="${1:-}"

    print_header "Running UI Tests"

    mkdir -p "$RESULTS_DIR/ui"

    # Get container workspace path (multi-project aware)
    local container_workspace=$(get_container_workspace)
    echo "  Container workspace: $container_workspace"

    # Get Unix-style project path for API calls
    local unix_project_path=$(get_unix_project_path)

    # Fix Windows path separators: convert backslashes to forward slashes
    feature_file="${feature_file//\\//}"

    # Test MCP port (default 8000)
    local test_mcp_port="${MCP_TEST_PORT:-8000}"

    if [ -n "$feature_file" ]; then
        # Extract just the filename if a full path was provided
        # Handle case where backslashes were stripped by shell (testsfeatures -> tests/features)
        local feature_name=$(basename "$feature_file")

        # If the feature_name contains "features" followed by more text, path separators were stripped
        # e.g., "testsfeaturesTransaction_Tracking_Dashboard.feature" -> "Transaction_Tracking_Dashboard.feature"
        if [[ "$feature_name" == *"features"* ]]; then
            # Extract everything after "features"
            local extracted="${feature_name#*features}"
            if [ -n "$extracted" ]; then
                feature_name="$extracted"
            fi
        fi

        echo "  Feature: $feature_name"

        # Read feature file content - try multiple locations
        local feature_path=""

        # First, try direct paths
        if [ -f "$TESTS_DIR/features/$feature_name" ]; then
            feature_path="$TESTS_DIR/features/$feature_name"
        elif [ -f "$PROJECT_ROOT/tests/features/$feature_name" ]; then
            feature_path="$PROJECT_ROOT/tests/features/$feature_name"
        elif [ -f "$PROJECT_ROOT/$feature_file" ]; then
            feature_path="$PROJECT_ROOT/$feature_file"
        fi

        # If still not found, try globbing
        if [ -z "$feature_path" ]; then
            echo "  Searching for feature file..."
            local glob_result=$(find "$TESTS_DIR/features" -name "*.feature" -type f 2>/dev/null | grep -i "${feature_name%.feature}" | head -1)
            if [ -n "$glob_result" ] && [ -f "$glob_result" ]; then
                feature_path="$glob_result"
                feature_name=$(basename "$feature_path")
                echo "  Found: $feature_name"
            fi
        fi

        if [ -f "$feature_path" ]; then
            # Validate feature file before running
            validate_feature_file "$feature_path"

            local feature_content
            feature_content=$(cat "$feature_path" | jq -Rs .)

            # Use HTTP API to run feature test (works with compiled Nuitka container)
            echo "  Running via Test MCP API..."
            local response
            response=$(curl -sf -X POST "http://localhost:${test_mcp_port}/run-feature" \
                -H "Content-Type: application/json" \
                -d "{
                    \"feature_content\": $feature_content,
                    \"projectPath\": \"$unix_project_path\",
                    \"featureFile\": \"${container_workspace}/tests/features/$feature_name\",
                    \"options\": {
                        \"base_url\": \"${UI_BASE_URL}\",
                        \"screenshot_on_failure\": true,
                        \"generate_report\": true,
                        \"headless\": ${BROWSER_HEADLESS:-true},
                        \"timeout\": ${BROWSER_TIMEOUT:-30000}
                    }
                }" 2>&1)

            # Save response to log
            echo "$response" | tee "$RESULTS_DIR/ui/${feature_name%.feature}.log"

            # Check for host_not_accessible status
            if echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"host_not_accessible"'; then
                show_host_accessibility_guide
                return $HOST_NOT_ACCESSIBLE_EXIT_CODE
            fi

            # Check for blocked host error patterns in response
            if echo "$response" | grep -q -i 'blocked request\|host.docker.internal.*not allowed\|allowedHosts'; then
                show_host_accessibility_guide
                return $HOST_NOT_ACCESSIBLE_EXIT_CODE
            fi

            # Check for invalid navigation step error
            if echo "$response" | grep -q -i 'INVALID NAVIGATION STEP\|Missing URL path'; then
                # Extract step text and suggested fix from response
                local step_text=$(echo "$response" | grep -o 'Your step:[^"]*' | head -1 | sed 's/Your step:[[:space:]]*//')
                local suggested=$(echo "$response" | grep -o 'Suggested:[^"]*' | head -1 | sed 's/Suggested:[[:space:]]*//')
                show_invalid_navigation_error "$step_text" "$suggested"
                return $INVALID_NAVIGATION_EXIT_CODE
            fi
        else
            print_error "Feature file not found: $feature_path"
            return 1
        fi
    else
        echo "  Running all feature files..."

        # Run all feature files via HTTP API
        for feature in "$TESTS_DIR/features"/*.feature; do
            if [ -f "$feature" ]; then
                feature_name=$(basename "$feature")
                echo ""
                echo -e "${BLUE}>>> Feature: $feature_name${NC}"

                # Validate feature file before running
                validate_feature_file "$feature"

                local feature_content
                feature_content=$(cat "$feature" | jq -Rs .)

                local response
                response=$(curl -sf -X POST "http://localhost:${test_mcp_port}/run-feature" \
                    -H "Content-Type: application/json" \
                    -d "{
                        \"feature_content\": $feature_content,
                        \"projectPath\": \"$unix_project_path\",
                        \"featureFile\": \"${container_workspace}/tests/features/$feature_name\",
                        \"options\": {
                            \"base_url\": \"${UI_BASE_URL}\",
                            \"screenshot_on_failure\": true,
                            \"generate_report\": true,
                            \"headless\": ${BROWSER_HEADLESS:-true},
                            \"timeout\": ${BROWSER_TIMEOUT:-30000}
                        }
                    }" 2>&1) || true

                # Save response to log
                echo "$response" | tee "$RESULTS_DIR/ui/${feature_name%.feature}.log"

                # Check for host_not_accessible status (stop immediately on first occurrence)
                if echo "$response" | grep -q '"status"[[:space:]]*:[[:space:]]*"host_not_accessible"'; then
                    show_host_accessibility_guide
                    return $HOST_NOT_ACCESSIBLE_EXIT_CODE
                fi

                # Check for blocked host error patterns in response
                if echo "$response" | grep -q -i 'blocked request\|host.docker.internal.*not allowed\|allowedHosts'; then
                    show_host_accessibility_guide
                    return $HOST_NOT_ACCESSIBLE_EXIT_CODE
                fi

                # Check for invalid navigation step error
                if echo "$response" | grep -q -i 'INVALID NAVIGATION STEP\|Missing URL path'; then
                    local step_text=$(echo "$response" | grep -o 'Your step:[^"]*' | head -1 | sed 's/Your step:[[:space:]]*//')
                    local suggested=$(echo "$response" | grep -o 'Suggested:[^"]*' | head -1 | sed 's/Suggested:[[:space:]]*//')
                    show_invalid_navigation_error "$step_text" "$suggested"
                    return $INVALID_NAVIGATION_EXIT_CODE
                fi
            fi
        done
    fi

    # Copy results from container (if any)
    # Multi-project: owner uses /results, additional projects use their workspace/test-results
    local container_results="/results"
    if [ "$container_workspace" != "/workspace" ]; then
        container_results="${container_workspace}/test-results"
    fi
    docker cp "${TEST_MCP_CONTAINER}:${container_results}/." "$RESULTS_DIR/" 2>/dev/null || true

    print_success "UI test results saved to: $RESULTS_DIR/ui/"
}

run_api_tests() {
    # API tests ALWAYS use database mode - fetch from api_library table
    # The api_library is populated by Dev MCP via pre-commit/pre-push hooks

    print_header "Running API Functional Tests"

    mkdir -p "$RESULTS_DIR/api"

    local test_mcp_port="${MCP_TEST_PORT:-8000}"
    local mcp_config_file="$SCRIPT_DIR/mcp-config.json"

    # Get ProjexLight configuration from env vars or mcp-config.json
    local api_url="${PROJEXLIGHT_API_URL:-}"
    local api_key="${PROJEXLIGHT_API_KEY:-}"
    local project_id="${PROJEXLIGHT_PROJECT_ID:-}"
    local feature_id="${FEATURE_ID:-}"
    local sprint_id="${SPRINT_ID:-}"

    # Read from mcp-config.json if not set in environment
    if [ -f "$mcp_config_file" ]; then
        if [ -z "$project_id" ]; then
            project_id=$(jq -r '.projectId // empty' "$mcp_config_file" 2>/dev/null)
        fi
        if [ -z "$sprint_id" ]; then
            sprint_id=$(jq -r '.sprintId // empty' "$mcp_config_file" 2>/dev/null)
        fi
    fi

    # API URL is required - api_library is the source of truth
    if [ -z "$api_url" ]; then
        print_error "PROJEXLIGHT_API_URL is required for API tests"
        echo "  API tests fetch definitions from ProjexLight api_library table."
        echo "  Make sure pre-push hook has synced your api_definitions to the database."
        echo ""
        echo "  Set PROJEXLIGHT_API_URL in mcp-server/.env file"
        exit 1
    fi

    # Try to get API key from running Dev MCP container if not set
    if [ -z "$api_key" ]; then
        # Find Dev MCP container dynamically (matches *dev*mcp* pattern, excludes test)
        local dev_container=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i 'dev' | grep -i 'mcp' | grep -v -i 'test' | head -1)
        if [ -n "$dev_container" ]; then
            api_key=$(docker exec "$dev_container" printenv PROJEXLIGHT_API_KEY 2>/dev/null || echo "")
            if [ -n "$api_key" ]; then
                echo "  [OK] Got API key from Dev MCP container ($dev_container)"
            fi
        fi
    fi

    # API key is required for authentication
    if [ -z "$api_key" ]; then
        print_warning "PROJEXLIGHT_API_KEY not set - API tests may fail to authenticate"
        echo "  Options:"
        echo "    1. Ensure Dev MCP container is running (it has the decrypted key)"
        echo "    2. Create a tenant API key: Settings > API Keys > Create Key"
        echo "    3. Set PROJEXLIGHT_API_KEY in mcp-server/.env file"
        echo ""
    fi

    # Project ID is required for api_library query
    if [ -z "$project_id" ]; then
        print_error "Project ID is required for API tests"
        echo "  Set PROJEXLIGHT_PROJECT_ID or ensure mcp-config.json has projectId"
        exit 1
    fi

    # Get API base URL from test-config.json (for the application under test)
    local api_base_url="${API_BASE_URL:-}"
    local current_env="${ENV_OVERRIDE:-${TEST_ENVIRONMENT:-development}}"
    if [ -z "$api_base_url" ] && [ -f "$CONFIG_FILE" ]; then
        # Try multiple field paths: direct fields, then environment-specific
        api_base_url=$(jq -r --arg env "$current_env" '
            .api_base_url // .apiBaseUrl //
            .environments[$env].apiUrl // .environments[$env].api_url //
            .environments.development.apiUrl // .environments.development.api_url //
            empty' "$CONFIG_FILE" 2>/dev/null)
        # Translate localhost to host.docker.internal for Docker containers
        if [ -n "$api_base_url" ]; then
            api_base_url=$(echo "$api_base_url" | sed 's/localhost/host.docker.internal/g')
        fi
    fi

    echo "  Mode: database (api_library)"
    echo "  ProjexLight API: $api_url"
    echo "  Project ID: $project_id"
    echo "  Feature ID: ${feature_id:-all}"
    echo "  Sprint ID: ${sprint_id:-current}"
    echo "  API Base URL: ${api_base_url:-from test-config.json}"
    echo "  Environment: ${ENV_OVERRIDE:-${TEST_ENVIRONMENT:-development}}"

    # Call Test MCP with database mode
    curl -sf -X POST "http://localhost:${test_mcp_port}/run-api-test-by-feature" \
        -H "Content-Type: application/json" \
        -d "{
            \"mode\": \"database\",
            \"feature_id\": \"$feature_id\",
            \"sprint_id\": \"$sprint_id\",
            \"projexlight_api_url\": \"$api_url\",
            \"projexlight_api_key\": \"$api_key\",
            \"project_id\": \"$project_id\",
            \"api_base_url\": \"$api_base_url\",
            \"environment\": \"${ENV_OVERRIDE:-${TEST_ENVIRONMENT:-development}}\",
            \"generate_report\": true,
            \"save_results\": ${SAVE_RESULTS:-false}
        }" 2>&1 | tee "$RESULTS_DIR/api/test_run.log"

    # Copy results from container
    local container_workspace=$(get_container_workspace)
    local container_results="/results"
    if [ "$container_workspace" != "/workspace" ]; then
        container_results="${container_workspace}/test-results"
    fi
    docker cp "${TEST_MCP_CONTAINER}:${container_results}/api_test_results.json" "$RESULTS_DIR/api/" 2>/dev/null || true
    docker cp "${TEST_MCP_CONTAINER}:${container_results}/api_test_report.html" "$RESULTS_DIR/api/" 2>/dev/null || true

    print_success "API test results saved to: $RESULTS_DIR/api/"
}

run_all_tests() {
    print_header "Running All Tests"

    run_ui_tests
    echo ""
    run_api_tests "" "all"

    print_header "Test Summary"
    echo "  UI Results: $RESULTS_DIR/ui/"
    echo "  API Results: $RESULTS_DIR/api/"
}

run_unified_tests() {
    local run_ui="${RUN_UI_TESTS:-true}"
    local run_api="${RUN_API_TESTS:-true}"

    print_header "Running Unified Tests (UI + API)"

    mkdir -p "$RESULTS_DIR/unified"

    echo "  Mode: $TEST_MODE"
    echo "  Run UI Tests: $run_ui"
    echo "  Run API Tests: $run_api"

    # Get container workspace path (multi-project aware)
    local container_workspace=$(get_container_workspace)
    echo "  Container workspace: $container_workspace"

    # Get Unix-style project path for API calls
    local unix_project_path=$(get_unix_project_path)

    # Test MCP port (default 8000)
    local test_mcp_port="${MCP_TEST_PORT:-8000}"

    if [ "$TEST_MODE" = "database" ]; then
        # Database mode - fetch from ProjexLight
        echo "  Fetching tests from ProjexLight..."

        local api_url="${PROJEXLIGHT_API_URL:-}"
        local api_key="${PROJEXLIGHT_API_KEY:-}"
        local project_id="${PROJEXLIGHT_PROJECT_ID:-}"
        local feature_id="${FEATURE_ID:-}"
        local sprint_id="${SPRINT_ID:-}"

        if [ -z "$api_url" ]; then
            print_error "PROJEXLIGHT_API_URL is required for database mode"
            exit 1
        fi

        curl -sf -X POST "http://localhost:${test_mcp_port}/run-unified-test" \
            -H "Content-Type: application/json" \
            -d "{
                \"mode\": \"database\",
                \"feature_id\": \"$feature_id\",
                \"sprint_id\": \"$sprint_id\",
                \"run_ui_tests\": $run_ui,
                \"run_api_tests\": $run_api,
                \"projexlight_api_url\": \"$api_url\",
                \"projexlight_api_key\": \"$api_key\",
                \"project_id\": \"$project_id\",
                \"environment\": \"${ENV_OVERRIDE:-${TEST_ENVIRONMENT:-development}}\",
                \"generate_report\": true,
                \"save_results\": ${SAVE_RESULTS:-false}
            }" 2>&1 | tee "$RESULTS_DIR/unified/test_run.log"
    else
        # Local mode - load from files
        echo "  Loading tests from local files..."
        echo "  Using UI Base URL: $UI_BASE_URL"
        echo "  Using API Base URL: $API_BASE_URL"

        curl -sf -X POST "http://localhost:${test_mcp_port}/run-unified-test" \
            -H "Content-Type: application/json" \
            -d "{
                \"mode\": \"local\",
                \"run_ui_tests\": $run_ui,
                \"run_api_tests\": $run_api,
                \"local_features_path\": \"${container_workspace}/tests/features\",
                \"local_api_path\": \"${container_workspace}/tests/api_definitions\",
                \"ui_base_url\": \"${UI_BASE_URL}\",
                \"api_base_url\": \"${API_BASE_URL}\",
                \"projectPath\": \"$unix_project_path\",
                \"generate_report\": true
            }" 2>&1 | tee "$RESULTS_DIR/unified/test_run.log"
    fi

    # Copy results from container
    local container_results="/results"
    if [ "$container_workspace" != "/workspace" ]; then
        container_results="${container_workspace}/test-results"
    fi
    docker cp "${TEST_MCP_CONTAINER}:${container_results}/unified_test_report.html" "$RESULTS_DIR/unified/" 2>/dev/null || true

    print_success "Unified test results saved to: $RESULTS_DIR/unified/"
}


show_status() {
    print_header "Test MCP Status"
    "$SCRIPT_DIR/setup-test-mcp.sh" status
}

show_usage() {
    echo ""
    echo "ProjexLight Test Runner"
    echo "======================="
    echo ""
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  ui [feature_file]      - Run UI/BDD tests (from local feature files)"
    echo "  api                    - Run API tests (from api_library database)"
    echo "  unified                - Run UI + API tests together"
    echo "  all                    - Run all tests"
    echo "  status                 - Check Test MCP status"
    echo ""
    echo "Options:"
    echo "  --login-email <email>  - Override test user email for this run"
    echo "                           (written to testCredentials.default.email)"
    echo "  --login-password <pw>  - Override test user password for this run"
    echo "                           (written to testCredentials.default.password)"
    echo "  --admin-email <email>  - Override admin user email for this run"
    echo "                           (written to testCredentials.admin.email)"
    echo "                           Required for endpoints gated by requiresRole:['admin']"
    echo "  --admin-password <pw>  - Override admin user password for this run"
    echo "                           (written to testCredentials.admin.password)"
    echo "  --env <name>           - Use specific environment from test-config.json"
    echo "                           (e.g., development, staging, production, qa, regression)"
    echo "  --feature-id <id>      - Feature ID to test"
    echo "  --sprint-id <id>       - Sprint ID to test"
    echo "  --ui-only              - Run only UI tests (unified command)"
    echo "  --api-only             - Run only API tests (unified command)"
    echo "  --mode <type>          - For unified: local (default) or database"
    echo ""
    echo "Examples:"
    echo ""
    echo "  # UI Tests (from local feature files)"
    echo "  $0 ui                                    # All UI tests"
    echo "  $0 ui --env staging                     # UI tests against staging"
    echo "  $0 ui lead-contact-management.feature   # Single feature"
    echo ""
    echo "  # API Tests (from api_library database)"
    echo "  $0 api                                   # All API tests"
    echo "  $0 api --env staging                    # API tests against staging"
    echo "  $0 api --env production                 # API tests against production"
    echo "  $0 api --feature-id abc123              # APIs for specific feature"
    echo "  $0 api --sprint-id sprint-456           # APIs for specific sprint"
    echo ""
    echo "  # Unified Tests (UI + API)"
    echo "  $0 unified                               # UI + API tests"
    echo "  $0 unified --env staging                # UI + API against staging"
    echo "  $0 api --login-email projexlight@gmail.com"
    echo "                                          # Use real mailbox for email-gated flows"
    echo "  $0 api --login-email test1@example.com --login-password 'MyPass@1234'"
    echo "                                          # Full override (email + password)"
    echo "  $0 api --admin-email qa_admin@example.com --admin-password 'AdminPass@1234'"
    echo "                                          # Set admin creds for role-gated endpoints"
    echo "  $0 unified --api-only                   # Only API via unified endpoint"
    echo "  $0 unified --ui-only                    # Only UI via unified endpoint"
    echo ""
    echo "  # All Tests"
    echo "  $0 all                                   # All UI + API tests"
    echo ""
    echo "Environment Variables (set in mcp-server/.env):"
    echo "  PROJEXLIGHT_API_URL      - ProjexLight API URL (required for API tests)"
    echo "  PROJEXLIGHT_API_KEY      - Tenant API key 'ak_...' (required for API tests)"
    echo "  PROJEXLIGHT_PROJECT_ID   - Project ID in ProjexLight"
    echo "  FEATURE_ID               - Feature ID to test"
    echo "  SPRINT_ID                - Sprint ID to test"
    echo "  UI_BASE_URL              - UI base URL (overrides test-config.json)"
    echo "  API_BASE_URL             - API base URL (overrides test-config.json)"
    echo ""
    echo "Setup:"
    echo "  1. Set PROJEXLIGHT_API_URL in mcp-server/.env"
    echo "  2. Create API key in ProjexLight: Settings > API Keys > Create Key"
    echo "  3. Set PROJEXLIGHT_API_KEY in mcp-server/.env"
    echo ""
    echo "Note: API tests always fetch definitions from api_library table."
    echo "      Run pre-push hook first to sync local api_definitions to database."
    echo ""
}

# Parse arguments
COMMAND="${1:-}"
shift || true

# Default values
DATASET_TYPE="all"
TEST_MODE="local"
FEATURE_OR_CATEGORY=""
RUN_UI_TESTS="true"
RUN_API_TESTS="true"
ENV_OVERRIDE=""  # Environment override from --env argument
LOGIN_EMAIL_OVERRIDE=""
LOGIN_PASSWORD_OVERRIDE=""
ADMIN_EMAIL_OVERRIDE=""
ADMIN_PASSWORD_OVERRIDE=""

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dataset)
            DATASET_TYPE="$2"
            shift 2
            ;;
        --mode)
            TEST_MODE="$2"
            shift 2
            ;;
        --env|--environment)
            ENV_OVERRIDE="$2"
            shift 2
            ;;
        --login-email)
            if [ -z "${2:-}" ]; then
                print_error "--login-email requires an email address"
                exit 1
            fi
            LOGIN_EMAIL_OVERRIDE="$2"
            shift 2
            ;;
        --login-password)
            if [ -z "${2:-}" ]; then
                print_error "--login-password requires a value"
                exit 1
            fi
            LOGIN_PASSWORD_OVERRIDE="$2"
            shift 2
            ;;
        --admin-email)
            if [ -z "${2:-}" ]; then
                print_error "--admin-email requires an email address"
                exit 1
            fi
            ADMIN_EMAIL_OVERRIDE="$2"
            shift 2
            ;;
        --admin-password)
            if [ -z "${2:-}" ]; then
                print_error "--admin-password requires a value"
                exit 1
            fi
            ADMIN_PASSWORD_OVERRIDE="$2"
            shift 2
            ;;
        --feature-id)
            export FEATURE_ID="$2"
            shift 2
            ;;
        --sprint-id)
            export SPRINT_ID="$2"
            shift 2
            ;;
        --ui-only)
            RUN_UI_TESTS="true"
            RUN_API_TESTS="false"
            shift
            ;;
        --api-only)
            RUN_UI_TESTS="false"
            RUN_API_TESTS="true"
            shift
            ;;
        *)
            FEATURE_OR_CATEGORY="$1"
            shift
            ;;
    esac
done

# Export for use in functions
export TEST_MODE
export RUN_UI_TESTS
export RUN_API_TESTS
export ENV_OVERRIDE

# Main
check_prerequisites

# Ensure config exists and apply any --login-email / --login-password overrides.
# ensure_test_config is a no-op if the file already exists.
ensure_test_config
apply_login_overrides

TEST_EXIT_CODE=0

case "$COMMAND" in
    ui)
        ensure_test_mcp
        check_sut_connectivity ui
        run_ui_tests "$FEATURE_OR_CATEGORY" || TEST_EXIT_CODE=$?
        ;;
    api)
        ensure_test_mcp
        check_sut_connectivity api
        # API tests always use database mode (api_library table)
        run_api_tests || TEST_EXIT_CODE=$?
        ;;
    unified)
        ensure_test_mcp
        check_sut_connectivity both
        run_unified_tests || TEST_EXIT_CODE=$?
        ;;
    all)
        ensure_test_mcp
        check_sut_connectivity both
        run_all_tests || TEST_EXIT_CODE=$?
        ;;
    status)
        show_status
        ;;
    *)
        show_usage
        exit 1
        ;;
esac

echo ""

# Show config check hint if config was just created (helps diagnose failures)
if [ "$CONFIG_CREATED_THIS_RUN" = "true" ]; then
    show_config_check_hint
fi

# Check for specific exit codes
if [ $TEST_EXIT_CODE -eq $HOST_NOT_ACCESSIBLE_EXIT_CODE ]; then
    # Host not accessible - show the guide from shell script too
    show_host_accessibility_guide
    print_error "Tests aborted: Client framework is blocking Docker requests"
    echo ""
    echo "  Please configure your client framework to allow 'host.docker.internal'"
    echo "  and restart your development server before re-running tests."
    echo ""
    exit $HOST_NOT_ACCESSIBLE_EXIT_CODE
elif [ $TEST_EXIT_CODE -eq $INVALID_NAVIGATION_EXIT_CODE ]; then
    print_error "Tests aborted: Feature file has invalid navigation step"
    echo ""
    echo "  Please fix your feature file and re-run tests."
    echo ""
    exit $INVALID_NAVIGATION_EXIT_CODE
elif [ $TEST_EXIT_CODE -eq 0 ]; then
    print_success "Test execution complete!"
else
    # Check test output for blocked host indicators
    if [ -f "$RESULTS_DIR/ui/"*.log ] 2>/dev/null; then
        if grep -q -i "blocked request\|host.docker.internal.*not allowed\|allowedHosts" "$RESULTS_DIR/ui/"*.log 2>/dev/null; then
            show_host_accessibility_guide
            print_error "Tests failed: Client framework appears to be blocking Docker requests"
            exit $HOST_NOT_ACCESSIBLE_EXIT_CODE
        fi
        # Check for invalid navigation step in logs
        if grep -q -i "INVALID NAVIGATION STEP\|Missing URL path" "$RESULTS_DIR/ui/"*.log 2>/dev/null; then
            print_error "Tests failed: Feature file has invalid navigation step (missing URL path)"
            exit $INVALID_NAVIGATION_EXIT_CODE
        fi
    fi

    print_warning "Test execution completed with errors (exit code: $TEST_EXIT_CODE)"
    exit $TEST_EXIT_CODE
fi

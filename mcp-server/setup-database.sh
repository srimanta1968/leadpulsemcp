#!/bin/bash
#
# ProjexLight Database Server Setup Script
# =========================================
# This script sets up the Database server based on the database type
# configured in mcp-config.json during CLI Export.
#
# Automatically supports:
#   - PostgreSQL
#   - MySQL
#   - MariaDB
#   - MongoDB
#   - Redis
#   - Cassandra
#   - DynamoDB (local)
#   - SQLite (no Docker needed)
#
# Prerequisites:
#   - Docker and Docker Compose installed
#   - mcp-config.json with databaseConfig section
#
# Usage:
#   ./setup-database.sh              # Start database
#   ./setup-database.sh start        # Start database
#   ./setup-database.sh stop         # Stop database
#   ./setup-database.sh restart      # Restart database
#   ./setup-database.sh status       # Check status
#   ./setup-database.sh logs         # View logs
#   ./setup-database.sh reset        # Stop, remove data, restart
#   ./setup-database.sh generate     # Generate docker-compose.yml
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[DB]${NC} $1"; }
warn() { echo -e "${YELLOW}[DB]${NC} $1"; }
error() { echo -e "${RED}[DB]${NC} $1" >&2; }

# Detect script and project directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Navigate up from scripts folder to mcp-server folder
if [[ "$SCRIPT_DIR" == *"scripts"* ]]; then
    MCP_DIR="$(dirname "$SCRIPT_DIR")"
    PROJECT_ROOT="$(dirname "$MCP_DIR")"
elif [[ "$SCRIPT_DIR" == *"mcp-server"* ]]; then
    MCP_DIR="$SCRIPT_DIR"
    PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
elif [[ "$SCRIPT_DIR" == *"mcp/dist"* ]] || [[ "$SCRIPT_DIR" == *"mcp\\dist"* ]]; then
    # Running from mcp/dist directory (development/exported package)
    MCP_DIR="$SCRIPT_DIR"
    PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
elif [ -f "$SCRIPT_DIR/docker-compose.yml" ] || [ -f "$SCRIPT_DIR/database-compose.yml" ]; then
    # Running from directory with compose files (dist or mcp-server)
    MCP_DIR="$SCRIPT_DIR"
    PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
else
    PROJECT_ROOT="$SCRIPT_DIR"
    MCP_DIR="$PROJECT_ROOT/mcp-server"
fi

# Generated compose file path
COMPOSE_FILE="$MCP_DIR/database-compose.yml"
CONFIG_FILE="$MCP_DIR/mcp-config.json"

# Determine init-scripts path (prefer project root, fallback to mcp-server)
get_init_scripts_path() {
    # Check if project root has init-scripts with files
    if [ -d "$PROJECT_ROOT/init-scripts" ] && [ "$(ls -A "$PROJECT_ROOT/init-scripts" 2>/dev/null)" ]; then
        echo "../init-scripts"
    else
        echo "./init-scripts"
    fi
}

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   ProjexLight Database Server Setup${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""

# Read database config from mcp-config.json or .env or use defaults
read_db_config() {
    # First try to read from .env file if it exists
    if [ -f "$MCP_DIR/.env" ]; then
        source "$MCP_DIR/.env" 2>/dev/null || true
    fi

    # Set defaults from .env or hardcoded defaults
    DB_TYPE="${DB_TYPE:-postgresql}"
    DB_HOST="${DB_HOST:-postgres}"
    DB_PORT="${DB_PORT:-5432}"
    DB_NAME="${DB_NAME:-appdb}"
    DB_USER="${DB_USER:-appuser}"
    DB_PASS="${DB_PASSWORD:-apppassword}"

    # Override with mcp-config.json if it exists
    if [ -f "$CONFIG_FILE" ]; then
        log "Reading config from: $CONFIG_FILE"

        # Parse database config using grep/sed (works without jq)
        local _DB_ENABLED=$(grep -o '"enabled"[[:space:]]*:[[:space:]]*[^,}]*' "$CONFIG_FILE" 2>/dev/null | grep -o '[^:]*$' | tr -d ' ')
        local _DB_TYPE=$(grep -o '"type"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"')
        local _DB_HOST=$(grep -o '"host"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"')
        local _DB_PORT=$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$CONFIG_FILE" 2>/dev/null | grep -o '[0-9]*$')
        local _DB_NAME=$(grep -o '"database"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"')
        local _DB_USER=$(grep -o '"username"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"')
        local _DB_PASS=$(grep -o '"password"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | grep -o '"[^"]*"$' | tr -d '"')

        # Handle if jq is available (more reliable parsing)
        if command -v jq &> /dev/null; then
            _DB_ENABLED=$(jq -r '.databaseConfig.enabled // true' "$CONFIG_FILE" 2>/dev/null || echo "true")
            _DB_TYPE=$(jq -r '.databaseConfig.type // ""' "$CONFIG_FILE" 2>/dev/null)
            _DB_HOST=$(jq -r '.databaseConfig.host // ""' "$CONFIG_FILE" 2>/dev/null)
            _DB_PORT=$(jq -r '.databaseConfig.port // ""' "$CONFIG_FILE" 2>/dev/null)
            _DB_NAME=$(jq -r '.databaseConfig.database // ""' "$CONFIG_FILE" 2>/dev/null)
            _DB_USER=$(jq -r '.databaseConfig.username // ""' "$CONFIG_FILE" 2>/dev/null)
            _DB_PASS=$(jq -r '.databaseConfig.password // ""' "$CONFIG_FILE" 2>/dev/null)
        fi

        # Only override if values are non-empty
        [ -n "$_DB_TYPE" ] && [ "$_DB_TYPE" != "null" ] && DB_TYPE="$_DB_TYPE"
        [ -n "$_DB_HOST" ] && [ "$_DB_HOST" != "null" ] && DB_HOST="$_DB_HOST"
        [ -n "$_DB_PORT" ] && [ "$_DB_PORT" != "null" ] && DB_PORT="$_DB_PORT"
        [ -n "$_DB_NAME" ] && [ "$_DB_NAME" != "null" ] && DB_NAME="$_DB_NAME"
        [ -n "$_DB_USER" ] && [ "$_DB_USER" != "null" ] && DB_USER="$_DB_USER"
        [ -n "$_DB_PASS" ] && [ "$_DB_PASS" != "null" ] && DB_PASS="$_DB_PASS"
    else
        warn "mcp-config.json not found, using defaults from .env or fallback values"
    fi

    log "Database type: ${CYAN}$DB_TYPE${NC}"
    log "Database name: $DB_NAME"
    log "Database user: $DB_USER"
}

# Generate docker-compose file based on database type
generate_compose() {
    read_db_config

    log "Generating docker-compose for $DB_TYPE..."

    case "$DB_TYPE" in
        postgresql|postgres)
            generate_postgres_compose
            ;;
        mysql)
            generate_mysql_compose
            ;;
        mariadb)
            generate_mariadb_compose
            ;;
        mongodb|mongo)
            generate_mongodb_compose
            ;;
        redis)
            generate_redis_compose
            ;;
        cassandra)
            generate_cassandra_compose
            ;;
        dynamodb)
            generate_dynamodb_compose
            ;;
        sqlite)
            log "SQLite does not require Docker - using local file"
            echo "# SQLite - No Docker Required" > "$COMPOSE_FILE"
            echo "# Database file: $DB_NAME.db" >> "$COMPOSE_FILE"
            return 0
            ;;
        *)
            error "Unsupported database type: $DB_TYPE"
            error "Supported: postgresql, mysql, mariadb, mongodb, redis, cassandra, dynamodb, sqlite"
            return 1
            ;;
    esac

    log "Generated: $COMPOSE_FILE"
}

generate_postgres_compose() {
    local INIT_SCRIPTS_PATH=$(get_init_scripts_path)
    log "Using init-scripts from: $INIT_SCRIPTS_PATH"

    cat > "$COMPOSE_FILE" << EOF
version: '3.8'
# Auto-generated PostgreSQL configuration
# Database: $DB_NAME

services:
  postgres:
    image: postgres:15-alpine
    container_name: projexlight-postgres
    environment:
      - POSTGRES_USER=${DB_USER}
      - POSTGRES_PASSWORD=${DB_PASS}
      - POSTGRES_DB=${DB_NAME}
    ports:
      - "${DB_PORT:-5432}:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ${INIT_SCRIPTS_PATH}:/docker-entrypoint-initdb.d
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
EOF
}

generate_mysql_compose() {
    local INIT_SCRIPTS_PATH=$(get_init_scripts_path)
    log "Using init-scripts from: $INIT_SCRIPTS_PATH"

    cat > "$COMPOSE_FILE" << EOF
version: '3.8'
# Auto-generated MySQL configuration
# Database: $DB_NAME

services:
  mysql:
    image: mysql:8.0
    container_name: projexlight-mysql
    environment:
      - MYSQL_ROOT_PASSWORD=${DB_PASS}
      - MYSQL_DATABASE=${DB_NAME}
      - MYSQL_USER=${DB_USER}
      - MYSQL_PASSWORD=${DB_PASS}
    ports:
      - "${DB_PORT:-3306}:3306"
    volumes:
      - mysql_data:/var/lib/mysql
      - ${INIT_SCRIPTS_PATH}:/docker-entrypoint-initdb.d
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u${DB_USER}", "-p${DB_PASS}"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  mysql_data:
EOF
}

generate_mariadb_compose() {
    local INIT_SCRIPTS_PATH=$(get_init_scripts_path)
    log "Using init-scripts from: $INIT_SCRIPTS_PATH"

    cat > "$COMPOSE_FILE" << EOF
version: '3.8'
# Auto-generated MariaDB configuration
# Database: $DB_NAME

services:
  mariadb:
    image: mariadb:10.11
    container_name: projexlight-mariadb
    environment:
      - MYSQL_ROOT_PASSWORD=${DB_PASS}
      - MYSQL_DATABASE=${DB_NAME}
      - MYSQL_USER=${DB_USER}
      - MYSQL_PASSWORD=${DB_PASS}
    ports:
      - "${DB_PORT:-3306}:3306"
    volumes:
      - mariadb_data:/var/lib/mysql
      - ${INIT_SCRIPTS_PATH}:/docker-entrypoint-initdb.d
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u${DB_USER}", "-p${DB_PASS}"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  mariadb_data:
EOF
}

generate_mongodb_compose() {
    local INIT_SCRIPTS_PATH=$(get_init_scripts_path)
    log "Using init-scripts from: $INIT_SCRIPTS_PATH"

    cat > "$COMPOSE_FILE" << EOF
version: '3.8'
# Auto-generated MongoDB configuration
# Database: $DB_NAME

services:
  mongodb:
    image: mongo:6.0
    container_name: projexlight-mongodb
    environment:
      - MONGO_INITDB_ROOT_USERNAME=${DB_USER}
      - MONGO_INITDB_ROOT_PASSWORD=${DB_PASS}
      - MONGO_INITDB_DATABASE=${DB_NAME}
    ports:
      - "${DB_PORT:-27017}:27017"
    volumes:
      - mongodb_data:/data/db
      - ${INIT_SCRIPTS_PATH}:/docker-entrypoint-initdb.d
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ismaster')"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  mongodb_data:
EOF
}

generate_redis_compose() {
    cat > "$COMPOSE_FILE" << EOF
version: '3.8'
# Auto-generated Redis configuration

services:
  redis:
    image: redis:7-alpine
    container_name: projexlight-redis
    command: redis-server --requirepass ${DB_PASS}
    ports:
      - "${DB_PORT:-6379}:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${DB_PASS}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  redis_data:
EOF
}

generate_cassandra_compose() {
    cat > "$COMPOSE_FILE" << EOF
version: '3.8'
# Auto-generated Cassandra configuration

services:
  cassandra:
    image: cassandra:4.1
    container_name: projexlight-cassandra
    environment:
      - CASSANDRA_CLUSTER_NAME=projexlight
      - CASSANDRA_DC=dc1
    ports:
      - "${DB_PORT:-9042}:9042"
    volumes:
      - cassandra_data:/var/lib/cassandra
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "cqlsh -e 'describe cluster'"]
      interval: 30s
      timeout: 10s
      retries: 5

volumes:
  cassandra_data:
EOF
}

generate_dynamodb_compose() {
    cat > "$COMPOSE_FILE" << EOF
version: '3.8'
# Auto-generated DynamoDB Local configuration

services:
  dynamodb:
    image: amazon/dynamodb-local:latest
    container_name: projexlight-dynamodb
    command: "-jar DynamoDBLocal.jar -sharedDb -dbPath /data"
    ports:
      - "${DB_PORT:-8000}:8000"
    volumes:
      - dynamodb_data:/data
    restart: unless-stopped

volumes:
  dynamodb_data:
EOF
}

# Validate setup
validate_setup() {
    log "Validating setup..."

    # Check Docker
    if ! docker info > /dev/null 2>&1; then
        error "Docker is not running. Please start Docker first."
        exit 1
    fi

    # Check Docker Compose (prefer V2 over V1 for compatibility)
    # V1 (docker-compose) has Python library conflicts on some Linux systems
    if docker compose version > /dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    elif docker-compose version > /dev/null 2>&1; then
        COMPOSE_CMD="docker-compose"
    else
        error "Docker Compose is not installed."
        exit 1
    fi

    # Note: mcp-config.json is optional - will use .env or defaults
    if [ -f "$CONFIG_FILE" ]; then
        log "Config file: $CONFIG_FILE"
    else
        warn "No mcp-config.json found - using .env or default values"
    fi

    log "Project root: $PROJECT_ROOT"
    log "MCP directory: $MCP_DIR"
}

# Get container name based on database type
get_container_name() {
    read_db_config
    case "$DB_TYPE" in
        postgresql|postgres) echo "projexlight-postgres" ;;
        mysql) echo "projexlight-mysql" ;;
        mariadb) echo "projexlight-mariadb" ;;
        mongodb|mongo) echo "projexlight-mongodb" ;;
        redis) echo "projexlight-redis" ;;
        cassandra) echo "projexlight-cassandra" ;;
        dynamodb) echo "projexlight-dynamodb" ;;
        *) echo "projexlight-db" ;;
    esac
}

# Check if container is running
container_running() {
    local container=$1
    docker ps --format '{{.Names}}' | grep -q "^${container}$"
}

# Check if container exists (running or stopped)
container_exists() {
    local container=$1
    docker ps -a --format '{{.Names}}' | grep -q "^${container}$"
}

# Create project-specific database in existing container
create_project_database() {
    local container=$1

    log "Ensuring database '$DB_NAME' exists in $DB_TYPE container..."

    case "$DB_TYPE" in
        postgresql|postgres)
            # Check if database exists, create if not
            if docker exec "$container" psql -U "$DB_USER" -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
                log "Database '$DB_NAME' already exists"
            else
                log "Creating database '$DB_NAME'..."
                docker exec "$container" psql -U "$DB_USER" -c "CREATE DATABASE \"$DB_NAME\"" 2>/dev/null || true
            fi
            ;;
        mysql|mariadb)
            docker exec "$container" mysql -u"$DB_USER" -p"$DB_PASS" -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\`" 2>/dev/null || true
            ;;
        mongodb|mongo)
            # MongoDB creates databases automatically on first use
            log "MongoDB will create database '$DB_NAME' on first use"
            ;;
        redis)
            log "Redis is ready for use"
            ;;
        cassandra)
            docker exec "$container" cqlsh -e "CREATE KEYSPACE IF NOT EXISTS $DB_NAME WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}" 2>/dev/null || true
            ;;
        dynamodb)
            log "DynamoDB Local is ready for use"
            ;;
    esac
}

# Start database
start_db() {
    validate_setup
    read_db_config

    if [ "$DB_TYPE" = "sqlite" ]; then
        log "SQLite does not require Docker container"
        log "Database file will be created at: $PROJECT_ROOT/$DB_NAME.db"
        return 0
    fi

    CONTAINER=$(get_container_name)

    # Check if container is already running
    if container_running "$CONTAINER"; then
        log "$DB_TYPE container is already running"
        log "Skipping creation - reusing existing container"

        # Create project-specific database in existing container
        create_project_database "$CONTAINER"

        echo ""
        show_status
        return 0
    fi

    # Check if container exists but stopped
    if container_exists "$CONTAINER"; then
        log "Starting existing $DB_TYPE container..."
        docker start "$CONTAINER"

        # Wait for database to be ready
        log "Waiting for database to be ready..."
        sleep 5
        wait_for_health "$DB_TYPE" "$CONTAINER"

        # Create project-specific database
        create_project_database "$CONTAINER"

        echo ""
        show_status
        return 0
    fi

    # Generate compose file if it doesn't exist
    if [ ! -f "$COMPOSE_FILE" ]; then
        generate_compose
    fi

    log "Creating new $DB_TYPE container..."

    # Create init-scripts directory
    mkdir -p "$MCP_DIR/init-scripts" 2>/dev/null || true

    cd "$MCP_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d

    # Wait for database to be ready
    log "Waiting for database to be ready..."
    sleep 5

    wait_for_health "$DB_TYPE" "$CONTAINER"

    echo ""
    show_status
}

# Wait for database health
wait_for_health() {
    local db_type=$1
    local container=$2

    for i in {1..30}; do
        case $db_type in
            postgresql|postgres)
                if docker exec "$container" pg_isready -U "${DB_USER}" > /dev/null 2>&1; then
                    log "PostgreSQL is ready!"
                    return 0
                fi
                ;;
            mysql|mariadb)
                if docker exec "$container" mysqladmin ping -u"${DB_USER}" -p"${DB_PASS}" > /dev/null 2>&1; then
                    log "$db_type is ready!"
                    return 0
                fi
                ;;
            mongodb|mongo)
                if docker exec "$container" mongosh --eval "db.adminCommand('ismaster')" > /dev/null 2>&1; then
                    log "MongoDB is ready!"
                    return 0
                fi
                ;;
            redis)
                if docker exec "$container" redis-cli -a "${DB_PASS}" ping > /dev/null 2>&1; then
                    log "Redis is ready!"
                    return 0
                fi
                ;;
            cassandra)
                if docker exec "$container" cqlsh -e "describe cluster" > /dev/null 2>&1; then
                    log "Cassandra is ready!"
                    return 0
                fi
                ;;
            dynamodb)
                if curl -sf "http://localhost:${DB_PORT:-8000}" > /dev/null 2>&1; then
                    log "DynamoDB is ready!"
                    return 0
                fi
                ;;
        esac
        echo -ne "${CYAN}[DB]${NC} Waiting for $db_type... $i/30\r"
        sleep 2
    done
    echo ""
    warn "Database may not be fully ready yet"
}

# Stop database
stop_db() {
    validate_setup
    read_db_config

    if [ "$DB_TYPE" = "sqlite" ]; then
        log "SQLite does not have a Docker container to stop"
        return 0
    fi

    if [ ! -f "$COMPOSE_FILE" ]; then
        warn "No docker-compose file found"
        return 0
    fi

    log "Stopping $DB_TYPE database..."
    cd "$MCP_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" down

    log "Database stopped"
}

# Reset database
reset_db() {
    validate_setup
    read_db_config

    if [ "$DB_TYPE" = "sqlite" ]; then
        warn "This will DELETE the SQLite database file!"
        read -p "Are you sure? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -f "$PROJECT_ROOT/$DB_NAME.db"
            log "SQLite database deleted"
        fi
        return 0
    fi

    warn "This will DELETE all $DB_TYPE database data!"
    read -p "Are you sure? (y/N): " -n 1 -r
    echo ""

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "Reset cancelled"
        return 0
    fi

    log "Stopping and removing database..."
    cd "$MCP_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" down -v

    log "Starting fresh database..."
    start_db
}

# Show status
show_status() {
    validate_setup
    read_db_config

    echo ""
    log "Database: ${CYAN}$DB_TYPE${NC}"
    echo ""

    if [ "$DB_TYPE" = "sqlite" ]; then
        if [ -f "$PROJECT_ROOT/$DB_NAME.db" ]; then
            log "SQLite database exists: $PROJECT_ROOT/$DB_NAME.db"
            ls -lh "$PROJECT_ROOT/$DB_NAME.db"
        else
            warn "SQLite database does not exist yet"
        fi
        return 0
    fi

    if [ ! -f "$COMPOSE_FILE" ]; then
        warn "No docker-compose file found. Run 'generate' first."
        return 0
    fi

    log "Container status:"
    echo ""
    cd "$MCP_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" ps

    echo ""
    log "Connection info:"
    case "$DB_TYPE" in
        postgresql|postgres)
            echo "  Host: localhost"
            echo "  Port: ${DB_PORT:-5432}"
            echo "  User: $DB_USER"
            echo "  Database: $DB_NAME"
            echo "  URL: postgresql://${DB_USER}:***@localhost:${DB_PORT:-5432}/${DB_NAME}"
            ;;
        mysql|mariadb)
            echo "  Host: localhost"
            echo "  Port: ${DB_PORT:-3306}"
            echo "  User: $DB_USER"
            echo "  Database: $DB_NAME"
            echo "  URL: mysql://${DB_USER}:***@localhost:${DB_PORT:-3306}/${DB_NAME}"
            ;;
        mongodb|mongo)
            echo "  Host: localhost"
            echo "  Port: ${DB_PORT:-27017}"
            echo "  User: $DB_USER"
            echo "  Database: $DB_NAME"
            echo "  URL: mongodb://${DB_USER}:***@localhost:${DB_PORT:-27017}/${DB_NAME}"
            ;;
        redis)
            echo "  Host: localhost"
            echo "  Port: ${DB_PORT:-6379}"
            echo "  URL: redis://:***@localhost:${DB_PORT:-6379}"
            ;;
        cassandra)
            echo "  Host: localhost"
            echo "  Port: ${DB_PORT:-9042}"
            ;;
        dynamodb)
            echo "  Host: localhost"
            echo "  Port: ${DB_PORT:-8000}"
            echo "  Endpoint: http://localhost:${DB_PORT:-8000}"
            ;;
    esac
    echo ""
}

# Show logs
show_logs() {
    validate_setup
    read_db_config

    if [ "$DB_TYPE" = "sqlite" ]; then
        log "SQLite does not have logs"
        return 0
    fi

    if [ ! -f "$COMPOSE_FILE" ]; then
        error "No docker-compose file found"
        return 1
    fi

    log "$DB_TYPE database logs:"
    echo ""
    cd "$MCP_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" logs --tail 100 -f
}

# Main command handler
case "${1:-start}" in
    start)
        start_db
        ;;
    stop)
        stop_db
        ;;
    restart)
        stop_db
        sleep 2
        start_db
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    reset)
        reset_db
        ;;
    generate)
        generate_compose
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|reset|generate}"
        echo ""
        echo "Commands:"
        echo "  start    - Start database container (default)"
        echo "  stop     - Stop database container"
        echo "  restart  - Restart database container"
        echo "  status   - Show container status and connection info"
        echo "  logs     - View database logs"
        echo "  reset    - Stop, remove data volume, and restart fresh"
        echo "  generate - Regenerate docker-compose.yml from mcp-config.json"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   Database Setup Complete${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""

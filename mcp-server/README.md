# ProjexLight MCP Server

**Enables your AI coding assistant to generate code with project-specific guidance.**

---

## What is MCP Server?

The **MCP (Model Context Protocol) Server** connects your AI coding assistant (Claude Code, Cursor, Cline, etc.) to ProjexLight, providing:

- **Project Context** - Your AI assistant knows your requirements, tech stack, and coding standards
- **Task Instructions** - Detailed guidance for each task in your sprint
- **Code Validation** - Automated checks before code is accepted
- **Git Quality Gates** - Duplicate detection and API testing on commit/push

---

## Quick Start

### Step 1: Start MCP Server

The MCP server image is pulled automatically from Docker Hub (`projexlight/mcp-server`).

```bash
cd mcp-server
docker-compose up -d
```

Wait about 30 seconds for services to initialize.

See [DOCKER_HUB.md](DOCKER_HUB.md) for detailed Docker Hub usage and configuration options.

### Step 2: Verify It's Running

```bash
curl http://localhost:8766/health
```

**Expected Response:**
```json
{
  "status": "healthy",
  "uptime": "...",
  "workspace": "/workspace"
}
```

### Step 3: Configure Your AI Coding Tool

Add the MCP server to your AI coding tool configuration.

**For Claude Code** (`~/.claude/settings.json` or project settings):
```json
{
  "mcpServers": {
    "projexlight": {
      "url": "http://localhost:8766"
    }
  }
}
```

**For Cursor/Cline:** Check their MCP configuration documentation.

### Step 4: Start Coding!

Navigate to your project directory and launch your AI coding assistant.

#### MCP-Enabled Tools (Claude, Goose, Cline, Antigravity)

These tools have native MCP support - instructions are fetched automatically:

```bash
cd your-project
claude  # or goose, cline, etc.
```
> "Read .claude/instructions/bootstrap.md and start"

#### HTTP API Tools (Cursor, Aider, Windsurf)

These tools use curl to fetch instructions from the MCP server:

```bash
cd your-project

# Start MCP server first
cd mcp-server && docker-compose up -d && cd ..

# Start your tool
cursor  # or aider, windsurf
```
> "Read .cursor/instructions/bootstrap.md and start"

The bootstrap.md contains curl commands to fetch rules and instructions.

---

### Continuing Work (After First Session)

```bash
cd your-project
claude  # or your preferred tool
```
> "Continue from where I left off"

**Or for specific tasks:**
> "Execute tasks 3-5"

---

### What Happens Automatically

1. AI reads bootstrap.md (minimal instructions)
2. Calls MCP server to get rules and task details
3. Generates code following fetched rules
4. Validates code before writing
5. Updates task progress

### Step 5: Initialize Git (When Ready)

```bash
cd ..  # Back to project root
git init
git remote add origin https://github.com/your-repo.git
```

Git hooks are automatically installed to check for duplicates and test APIs.

---

## Project Structure

```
your-project/
├── README.md                     # Project README
├── .claude/
│   └── instructions/
│       └── bootstrap.md          # Instructions for your AI assistant
├── .projexlight/
│   └── context/
│       ├── requirements.md       # Project requirements
│       ├── sprint-context.json   # Sprint configuration
│       └── task-list.json        # Tasks to complete
├── init-scripts/                 # Database initialization scripts
├── mcp-server/                   # MCP Server (this folder)
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── mcp-server               # Compiled server executable
│   └── README.md                # This file
└── src/                         # Your generated code goes here
```

---

## Git Hooks (Automatic)

When you run `git init`, the MCP server automatically installs quality gates:

| Hook | What It Does |
|------|--------------|
| **Pre-commit** | Scans for duplicate APIs and blocks if found |
| **Pre-push** | Tests your APIs and reports results |

### Bypass Hooks (Emergency Only)

```bash
git commit --no-verify -m "Emergency fix"
git push --no-verify
```

---

## Monitoring & Logs

### Health Check
```bash
curl http://localhost:8766/health
```

### View Logs

**Option 1: Via HTTP API (Recommended)**
```bash
# Get log directory and file locations
curl http://localhost:8766/logs

# View server logs (last 100 lines)
curl http://localhost:8766/logs/server

# View error logs (last 200 lines)
curl http://localhost:8766/logs/errors?lines=200

# View all logs combined
curl http://localhost:8766/logs/all?lines=100
```

**Option 2: Via Docker**
```bash
# View container stdout logs
docker logs projexlight-mcp

# Follow logs in real-time
docker logs -f projexlight-mcp
```

**Option 3: Direct File Access**

Logs are stored in your project's `.mcp-logs/` directory (accessible in your workspace):
```bash
# List log files
ls -la .mcp-logs/

# View main server log
cat .mcp-logs/mcp-server-YYYYMMDD-HHMMSS.log

# View latest server log (symlink)
cat .mcp-logs/latest-server.log
```

### Log Types

| Log Type | Description | HTTP Endpoint |
|----------|-------------|---------------|
| `server` | Main server activity | `/logs/server` |
| `activity` | File change detection | `/logs/activity` |
| `reviews` | Code review results | `/logs/reviews` |
| `errors` | Error messages | `/logs/errors` |

---

## System Requirements

- **Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux)
- **Git** for version control
- **AI Coding Tool** with MCP support (Claude Code, Cursor, Cline, etc.)

---

## Troubleshooting

### MCP Server Won't Start

```bash
# Check Docker is running
docker ps

# View logs for errors
docker logs projexlight-mcp

# Restart
docker-compose down && docker-compose up -d
```

### AI Assistant Can't Connect

1. Verify MCP server is running: `curl http://localhost:8766/health`
2. Check your AI tool's MCP configuration
3. Restart your AI coding tool after config changes

### Git Hooks Not Working

```bash
# Check hooks status
curl http://localhost:8766/hooks/status

# Manually install hooks
curl -X POST http://localhost:8766/hooks/install
```

---

## Stopping Services

```bash
cd mcp-server
docker-compose down
```

To remove data volumes too:
```bash
docker-compose down -v
```

---

## Additional Documentation

- **[DOCKER_HUB.md](DOCKER_HUB.md)** - Docker Hub image usage guide
- **[QUICK_START.md](QUICK_START.md)** - Detailed setup guide
- **[DEBUGGING.md](DEBUGGING.md)** - Troubleshooting and logs
- **[HOOKS_INSTALLATION.md](HOOKS_INSTALLATION.md)** - Git hooks details

---

## Support

1. Check **[DEBUGGING.md](DEBUGGING.md)** for common solutions
2. View logs: `docker logs projexlight-mcp`
3. Contact ProjexLight support through the platform

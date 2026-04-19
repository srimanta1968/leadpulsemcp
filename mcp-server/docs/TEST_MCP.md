# ProjexLight Test MCP Guide

The **Test MCP** is the test-execution side of ProjexLight's MCP server pair. It runs in Docker on your local machine, executes UI and API tests against your locally-running web app (the SUT), and writes results back to the ProjexLight dashboard.

This guide consolidates everything you need to run, configure, and troubleshoot the Test MCP. For code-generation workflows and git hooks (pre-commit duplicate detection, pre-push API testing), see [`DEV_MCP.md`](./DEV_MCP.md). For framework-specific SUT setup (binding your web app to `0.0.0.0`), see [`SUT_SETUP_GUIDE.md`](./SUT_SETUP_GUIDE.md).

## Table of Contents

- [Authentication](#authentication)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [MCP Server Setup](#mcp-server-setup)
- [UI Tests (Feature Files)](#ui-tests-feature-files)
- [API Functional Tests](#api-functional-tests)
- [Test Results](#test-results)
- [Configuration](#configuration)
  - [Test Configuration File (test-config.json)](#test-configuration-file-test-configjson)
  - [Test Credentials (Login/Registration Flow)](#test-credentials-loginregistration-flow)
  - [Environment Variables](#environment-variables)
- [Remote Environments (LAN, Staging, Production)](#remote-environments-lan-staging-production)
  - [Scenario A: Local dev on your machine](#scenario-a-local-dev-on-your-machine)
  - [Scenario B: Coworker's machine on the LAN](#scenario-b-coworkers-machine-on-the-lan)
  - [Scenario C: Cloud QA / staging](#scenario-c-cloud-qa--staging)
  - [Scenario D: Cloud production (read-only smoke tests)](#scenario-d-cloud-production-read-only-smoke-tests)
  - [Authentication for remote environments](#authentication-for-remote-environments)
  - [What behaves differently in remote mode](#what-behaves-differently-in-remote-mode)
- [Troubleshooting](#troubleshooting)

---

## Authentication

The Test MCP supports **two authentication methods**:

### Method 1: mcp-config.json (Recommended for Developers)

When using CLI export, the `mcp-config.json` file contains encrypted API keys that are automatically decrypted:

```
your-project/
├── mcp-server/
│   ├── mcp-config.json      # Encrypted config (from CLI export)
│   └── ...
```

The Test MCP will automatically:
1. Detect `mcp-config.json` on startup
2. Decrypt using the project ID
3. Configure ProjexLight API key and LLM settings

### Method 2: Environment Variables (For QA/Production)

For QA teams running tests without CLI export, set environment variables directly:

```bash
# Required for ProjexLight API access
export PROJEXLIGHT_API_KEY=your_api_key

# Required for LLM-based self-healing
export OPENAI_API_KEY=your_openai_key

# Optional LLM settings
export LLM_PROVIDER=openai
export LLM_MODEL_REVIEW=gpt-4o-mini
```

### Priority Order

1. **mcp-config.json** - If present, used first (decrypted automatically)
2. **Environment Variables** - Fallback if no config file found

### Checking Configuration Status

```bash
# Check which auth method is active
curl http://localhost:8000/config-status
```

Response:
```json
{
  "config_source": "mcp_config",
  "has_projexlight_key": true,
  "has_llm_key": true,
  "llm_provider": "openai",
  "project_id": "d90b34b6..."
}
```

---

## Prerequisites

1. **Docker** installed and running
2. **Tests folder** with feature files and API definitions:
   ```
   your-project/
   ├── tests/
   │   ├── features/           # BDD/Gherkin feature files
   │   │   ├── lead-contact-management.feature
   │   │   └── ...
   │   └── api_definitions/    # API test definitions
   │       ├── auth/
   │       ├── leads/
   │       └── ...
   └── mcp-server/             # MCP server files (from CLI export)
       ├── setup-all.sh
       ├── run-all-tests.sh
       └── ...
   ```

---

## Quick Start

```bash
cd your-project/mcp-server

# 1. Start all MCP services
./setup-all.sh

# 2. Run all UI tests
./run-all-tests.sh ui

# 3. Run all API tests
./run-all-tests.sh api

# 4. Run ALL tests (UI + API)
./run-all-tests.sh all

# 5. Check results
ls ../test-results/
```

---

## MCP Server Setup

### Docker Hub Images

| Image | Port | Purpose |
|-------|------|---------|
| `projexlight/projex-dev-mcp` | 8766 | Code review, development assistance |
| `projexlight/projex-test-mcp` | 8000 | UI testing, API functional tests |

### Start/Stop Commands

```bash
# Start all services (recommended)
./setup-all.sh

# Check status
./setup-all.sh --status

# Start Test MCP only
./setup-test-mcp.sh start

# View logs
./setup-test-mcp.sh logs

# Stop
./setup-test-mcp.sh stop

# Update to latest image
./setup-test-mcp.sh update
```

---

## Test Runner

The `run-all-tests.sh` script provides a unified interface for all testing:

```bash
# Show help
./run-all-tests.sh

# Check Test MCP status
./run-all-tests.sh status

# Run all tests (UI + API)
./run-all-tests.sh all
```

### Commands

| Command | Description |
|---------|-------------|
| `ui [feature_file]` | Run UI/BDD tests |
| `api [category]` | Run API functional tests |
| `unified` | Run UI + API tests together |
| `all` | Run all tests |
| `status` | Check Test MCP status |

### Options

| Option | Description |
|--------|-------------|
| `--env <name>` | Use specific environment from test-config.json (e.g., development, staging, qa, production) |
| `--mode local\|database` | Execution mode (default: local) |
| `--dataset all\|positive\|negative` | Filter tests by dataset type |
| `--feature-id <id>` | Feature ID to test (database mode) |
| `--sprint-id <id>` | Sprint ID to test (database mode) |
| `--ui-only` | Run only UI tests (unified command) |
| `--api-only` | Run only API tests (unified command) |

### Examples by Mode

#### Local Mode (from project files)

```bash
# UI tests
./run-all-tests.sh ui                                # All UI tests (development env)
./run-all-tests.sh ui --env staging                  # UI tests against staging
./run-all-tests.sh ui login.feature                  # Single feature

# API tests
./run-all-tests.sh api                               # All API tests (development env)
./run-all-tests.sh api --env staging                 # API tests against staging
./run-all-tests.sh api --env qa                      # API tests against QA environment
./run-all-tests.sh api --env production              # API tests against production
./run-all-tests.sh api auth                          # Auth category only
./run-all-tests.sh api --dataset negative            # Negative test cases

# Unified tests
./run-all-tests.sh unified                           # UI + API together
./run-all-tests.sh unified --env staging             # UI + API against staging
./run-all-tests.sh unified --api-only                # Only API via unified
./run-all-tests.sh unified --ui-only                 # Only UI via unified
```

#### Using Custom Environments

Add custom environments to `test-config.json`:

```json
{
  "environments": {
    "development": {
      "baseUrl": "http://localhost:3000",
      "apiUrl": "http://localhost:3000"
    },
    "qa": {
      "baseUrl": "https://qa.example.com",
      "apiUrl": "https://api-qa.example.com",
      "description": "QA environment"
    },
    "regression": {
      "baseUrl": "https://regression.example.com",
      "apiUrl": "https://api-regression.example.com",
      "description": "Regression testing"
    }
  }
}
```

Then run:
```bash
./run-all-tests.sh api --env qa
./run-all-tests.sh api --env regression
./run-all-tests.sh unified --env qa
```

#### Database Mode (from ProjexLight)

Requires environment variables:
```bash
export PROJEXLIGHT_API_URL=https://api.projexlight.com
export PROJEXLIGHT_API_KEY=your_api_key
export PROJEXLIGHT_PROJECT_ID=709c4270
```

```bash
# API tests from database
./run-all-tests.sh api --mode database
./run-all-tests.sh api --mode database --feature-id fb9c4d72-80b3-483d-ba63-7c71ea2fc46d
./run-all-tests.sh api --mode database --sprint-id sprint-123

# Unified tests from database
./run-all-tests.sh unified --mode database
./run-all-tests.sh unified --mode database --feature-id abc123
./run-all-tests.sh unified --mode database --api-only --feature-id abc123
```

### Environment Variables

**URL Configuration Priority:**
1. `--env <name>` flag reads URLs from `test-config.json` environments
2. Environment variables (`UI_BASE_URL`, `API_BASE_URL`) override config
3. Default: `development` environment from `test-config.json`

| Variable | Default | Description |
|----------|---------|-------------|
| `UI_BASE_URL` | From test-config.json | Target UI application URL (overrides config) |
| `API_BASE_URL` | From test-config.json | Target API base URL (overrides config) |
| `HEADLESS` | `true` | Run browser in headless mode |
| `RECORD_VIDEO` | `false` | Record video of UI tests |
| `TEST_MCP_IMAGE` | `projexlight/projex-test-mcp:latest` | Docker image |

**For Database Mode:**

| Variable | Description |
|----------|-------------|
| `PROJEXLIGHT_API_URL` | ProjexLight API URL |
| `PROJEXLIGHT_API_KEY` | ProjexLight API key |
| `PROJEXLIGHT_PROJECT_ID` | Project ID in ProjexLight |
| `FEATURE_ID` | Feature ID to test (optional) |
| `SPRINT_ID` | Sprint ID to test (optional) |
| `SAVE_RESULTS` | Save results back to ProjexLight (`true`/`false`) |

---

## UI Tests (Feature Files)

### Run All UI Tests

```bash
./run-all-tests.sh ui
```

### Run Single Feature (by File Name)

```bash
# With .feature extension
./run-all-tests.sh ui lead-contact-management.feature

# Without extension (auto-added)
./run-all-tests.sh ui lead-contact-management
```

### UI Test Options

```bash
# Set target application URL
BASE_URL=http://localhost:3000 ./run-all-tests.sh ui

# Run with visible browser (not headless)
HEADLESS=false ./run-all-tests.sh ui

# Record video of test execution
RECORD_VIDEO=true ./run-all-tests.sh ui

# Combined options
BASE_URL=http://myapp.com HEADLESS=false RECORD_VIDEO=true ./run-all-tests.sh ui lead-contact-management.feature
```

---

## API Functional Tests

### Run All API Tests

```bash
./run-all-tests.sh api
```

### Run Specific Category

```bash
./run-all-tests.sh api auth
./run-all-tests.sh api leads
./run-all-tests.sh api activities
```

### Dataset Filtering

The API test runner supports filtering test cases by type:

```bash
# Run ALL test cases (default)
./run-all-tests.sh api --dataset all

# Run only POSITIVE tests (2xx success responses)
./run-all-tests.sh api --dataset positive

# Run only NEGATIVE tests (4xx/5xx error responses)
./run-all-tests.sh api --dataset negative
```

### Dataset Types Explained

| Dataset | Description | Example Test Cases |
|---------|-------------|-------------------|
| `all` | All test cases | Everything |
| `positive` | Success scenarios (2xx) | "Login with valid credentials" |
| `negative` | Error scenarios (4xx, 5xx) | "Login with invalid password", "Missing required field" |

### Combine Category and Dataset

```bash
# Run positive auth tests only
./run-all-tests.sh api auth --dataset positive

# Run negative leads tests only
./run-all-tests.sh api leads --dataset negative
```

### Set API Base URL

```bash
API_BASE_URL=http://localhost:3020 ./run-all-tests.sh api
API_BASE_URL=https://api.myapp.com ./run-all-tests.sh api auth
```

---

## Test Results

Results are saved to `test-results/` folder:

```
your-project/
└── test-results/
    ├── ui/
    │   ├── lead-contact-management.log
    │   ├── lead-contact-management_report.json
    │   ├── screenshots/
    │   └── summary.json
    └── api/
        ├── auth_tests.log
        ├── auth_report.json
        ├── leads_report.json
        └── api_summary.json
```

### View Results

```bash
# UI test summary
cat test-results/ui/summary.json

# API test summary
cat test-results/api/api_summary.json
```

### Sample API Summary

```json
{
  "timestamp": "2024-01-01T12:00:00Z",
  "dataset_type": "all",
  "categories": 5,
  "total_tests": 25,
  "passed": 23,
  "failed": 2,
  "pass_rate": 92.0,
  "category_results": [
    { "category": "auth", "total": 3, "passed": 3, "failed": 0 },
    { "category": "leads", "total": 8, "passed": 7, "failed": 1 }
  ]
}
```

---

## Configuration

### Test Configuration File (test-config.json)

The test runner uses a configuration file at `tests/config/test-config.json`. This file is **automatically created** when you run:
- `./setup-test-mcp.sh start`
- `./setup-all.sh`
- `./run-all-tests.sh` (if config doesn't exist)

#### Location

```
your-project/
├── tests/
│   ├── config/
│   │   ├── test-config.json       # Main test configuration
│   │   └── credentials-cache.json # Auto-saved registration credentials
│   └── features/
│       └── *.feature
└── mcp-server/
```

#### Configuration Structure

```json
{
  "version": "1.0.0",
  "environments": {
    "development": {
      "baseUrl": "http://localhost:3000",
      "apiUrl": "http://localhost:5000",
      "description": "Local development environment"
    },
    "staging": {
      "baseUrl": "https://staging.example.com",
      "apiUrl": "https://api.staging.example.com",
      "description": "Staging environment for pre-production testing"
    },
    "production": {
      "baseUrl": "https://www.example.com",
      "apiUrl": "https://api.example.com",
      "description": "Production environment (read-only tests only)"
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
      "password": "DefaultTestPass123!"
    },
    "registered": null
  },
  "featureValidation": {
    "enabled": true,
    "autoRegenerate": true,
    "retryFailedTests": true,
    "maxRetries": 3
  }
}
```

#### Active Environment Selection

The `activeEnvironment` field determines which environment's URLs are used for testing:

```json
"activeEnvironment": "development"
```

| Environment | When to Use |
|-------------|-------------|
| `development` | Local testing against localhost |
| `staging` | Pre-production testing |
| `production` | Read-only smoke tests only |

**To switch environments**, simply change the `activeEnvironment` value:

```json
// For staging tests
"activeEnvironment": "staging"

// For production smoke tests
"activeEnvironment": "production"
```

#### Automatic localhost Translation (Docker)

When tests run inside the Docker container, `localhost` URLs cannot reach your host machine. The `run-all-tests.sh` script **automatically translates** localhost URLs to `host.docker.internal`:

```
localhost:3000  →  host.docker.internal:3000
localhost:5000  →  host.docker.internal:5000
```

**This means you can keep `localhost` in your config** - the translation happens automatically at runtime.

#### Cloud Environment Detection

The automatic translation is **skipped** when running in cloud environments (AWS, Azure, GCP) where `host.docker.internal` doesn't apply:

| Cloud Provider | Detection Method |
|----------------|------------------|
| AWS ECS/Fargate | `AWS_EXECUTION_ENV` or `ECS_CONTAINER_METADATA_URI` |
| Azure Container | `WEBSITE_INSTANCE_ID` or `AZURE_CONTAINER_APP_NAME` |
| Google Cloud Run | `K_SERVICE` or `GOOGLE_CLOUD_PROJECT` |

You can also **manually skip translation** by setting:

```bash
SKIP_LOCALHOST_TRANSLATION=true ./run-all-tests.sh api
```

#### Adding Custom Environments

You can add custom environments for different testing scenarios:

```json
"environments": {
  "development": {
    "baseUrl": "http://localhost:5173",
    "apiUrl": "http://localhost:5001",
    "description": "Local Vite dev server"
  },
  "docker-local": {
    "baseUrl": "http://host.docker.internal:5173",
    "apiUrl": "http://host.docker.internal:5001",
    "description": "Explicit Docker-to-host (if auto-translate disabled)"
  },
  "ci": {
    "baseUrl": "http://app:3000",
    "apiUrl": "http://api:5000",
    "description": "CI/CD environment with service names"
  }
}
```

### Test Credentials (Login/Registration Flow)

The `testCredentials` section enables credential sharing between registration and login scenarios.

#### Credential Placeholders

Use these placeholders in your feature files:

| Placeholder | Description |
|-------------|-------------|
| `${random_email}` | Generates unique email (e.g., `test_1705234567890_abc@example.com`) |
| `${random_password}` | Generates secure password (e.g., `Test@1234`) |
| `${registered:email}` | Email from last successful registration |
| `${registered:password}` | Password from last successful registration |
| `${default:email}` | Default email from config |
| `${default:password}` | Default password from config |
| `${login:email}` | **Smart**: Uses registered if available, else default |
| `${login:password}` | **Smart**: Uses registered if available, else default |

#### Example: Registration then Login

```gherkin
Feature: User Authentication

  @scenario_type:UI
  Scenario: Register new user
    Given I navigate to "/register"
    When I fill "email" with "${random_email}"
    And I fill "password" with "${random_password}"
    And I fill "confirmPassword" with "${random_password}"
    And I click "Register"
    Then I should see "Registration successful"
    # Credentials auto-saved on success!

  @scenario_type:UI
  Scenario: Login with registered user
    Given I navigate to "/login"
    When I fill "email" with "${login:email}"
    And I fill "password" with "${login:password}"
    And I click "Login"
    Then I should see "Welcome"
```

#### How Auto-Save Works

1. **Registration scenario runs** with `${random_email}` and `${random_password}`
2. **If registration succeeds**, credentials are automatically saved to:
   - `testCredentials.registered` in `test-config.json`
   - `credentials-cache.json` for cross-session persistence
3. **Login scenario uses** `${login:email}` which:
   - First tries `registered.email` (from successful registration)
   - Falls back to `default.email` (from config) if not available

#### Setting Default Credentials

Edit `tests/config/test-config.json` to set fallback credentials:

```json
{
  "testCredentials": {
    "default": {
      "email": "qa_test_user@yourcompany.com",
      "password": "YourTestPassword123!"
    }
  }
}
```

These defaults are used when:
- No registration has been performed
- Registration scenario failed
- You want to test with a known existing user

### OAuth Bypass (Skip OAuth Login Flow)

For applications using OAuth (Google, GitHub, Microsoft, etc.), you can bypass the OAuth redirect flow and inject tokens directly. This makes tests faster and more reliable.

#### Enabling OAuth Bypass

Edit `tests/config/test-config.json`:

```json
{
  "authentication": {
    "method": "oauth_bypass",
    "oauth": {
      "bypassEnabled": true,
      "provider": "google",
      "tokens": {
        "accessToken": "your_jwt_access_token_here",
        "refreshToken": "your_refresh_token_here",
        "idToken": "your_id_token_here"
      }
    }
  }
}
```

#### How to Get Tokens

1. **Login manually** to your application in a browser
2. Open **DevTools** (F12) → **Application** tab → **Local Storage** or **Session Storage**
3. Find and copy the token values (usually `access_token`, `id_token`, etc.)
4. Paste them into `test-config.json`

Alternatively, check **Network** tab → find an API request → copy the `Authorization` header value.

#### For API Tests (Header Injection)

Tokens are automatically injected into API request headers:

```json
"oauth": {
  "bypassEnabled": true,
  "tokens": {
    "accessToken": "eyJhbGciOiJSUzI1NiIs..."
  },
  "apiHeaders": {
    "Authorization": "Bearer ${oauth:accessToken}"
  }
}
```

All API requests will include: `Authorization: Bearer <your_token>`

#### For UI Tests (Browser Storage Injection)

Tokens are injected into browser storage before tests run:

```json
"oauth": {
  "bypassEnabled": true,
  "tokens": {
    "accessToken": "your_token",
    "idToken": "your_id_token"
  },
  "browserStorage": {
    "type": "localStorage",
    "keys": {
      "accessToken": "access_token",
      "idToken": "id_token",
      "user": "user_info"
    }
  },
  "mockUser": {
    "id": "test-user-123",
    "email": "testuser@example.com",
    "name": "Test User",
    "roles": ["user"]
  }
}
```

This injects:
- `localStorage.setItem("access_token", "<your_token>")`
- `localStorage.setItem("user_info", JSON.stringify(mockUser))`

#### OAuth Placeholders

Use these in feature files or API definitions:

| Placeholder | Description |
|-------------|-------------|
| `${oauth:accessToken}` | Access token from config |
| `${oauth:refreshToken}` | Refresh token from config |
| `${oauth:idToken}` | ID token (OIDC) |
| `${oauth:user.id}` | Mock user ID |
| `${oauth:user.email}` | Mock user email |

#### Supported OAuth Providers

- Google
- GitHub
- Microsoft (Azure AD)
- Okta
- Auth0
- Custom (any OAuth 2.0 / OIDC provider)

#### Token Expiration & Auto-Refresh

Tokens expire. The test runner automatically checks token expiration before running tests and can refresh tokens automatically.

**Setting Token Expiration:**

```json
{
  "authentication": {
    "oauth": {
      "tokens": {
        "accessToken": "your_access_token",
        "refreshToken": "your_refresh_token",
        "expiresAt": "2024-12-31T23:59:59Z"
      }
    }
  }
}
```

`expiresAt` supports:
- ISO string: `"2024-12-31T23:59:59Z"`
- Unix timestamp (seconds): `1735689599`
- Unix timestamp (milliseconds): `1735689599000`

**Automatic Token Refresh:**

To enable automatic refresh, configure the `tokenRefresh` section:

```json
{
  "authentication": {
    "oauth": {
      "tokens": {
        "accessToken": "...",
        "refreshToken": "...",
        "expiresAt": "..."
      },
      "tokenRefresh": {
        "endpoint": "https://oauth2.googleapis.com/token",
        "clientId": "your-client-id",
        "clientSecret": "your-client-secret",
        "method": "POST",
        "bodyFormat": "form"
      }
    }
  }
}
```

The test runner will:
1. Check if token expires within 5 minutes
2. Automatically refresh using the refresh token
3. Save new tokens back to `test-config.json`
4. Continue tests with the new token

**If Refresh is Not Configured:**

If tests start failing with 401 errors and no refresh endpoint is configured:
1. Login manually again
2. Copy fresh tokens (access + refresh)
3. Update `test-config.json` with new tokens and expiry time

**For CI/CD Pipelines:**

- Configure `tokenRefresh` for automatic refresh
- Use service account tokens (longer expiry)
- Store tokens in CI secrets, not committed files

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BASE_URL` | `http://localhost:3000` | Target UI application URL |
| `API_BASE_URL` | `http://localhost:3020` | Target API base URL |
| `HEADLESS` | `true` | Run browser in headless mode |
| `RECORD_VIDEO` | `false` | Record video of UI tests |
| `TAKE_SCREENSHOTS` | `true` | Capture screenshots on failure |
| `TEST_MCP_IMAGE` | `projexlight/projex-test-mcp:latest` | Docker image |

### Example with Environment Variables

```bash
# Run UI tests against staging
BASE_URL=https://staging.myapp.com HEADLESS=false ./run-all-tests.sh ui

# Run API tests against production
API_BASE_URL=https://api.myapp.com ./run-all-tests.sh api --dataset positive
```

---

## Remote Environments (LAN, Staging, Production)

The test runner handles four deployment targets with one command — you just pass `--env <name>`. Everything else adapts automatically: auto-detect is skipped for remote environments, the `localhost` → `host.docker.internal` rewrite becomes a no-op for public URLs, and `check-sut.sh` switches from local bind-address introspection to pure HTTP probing.

### Overview — one table, four scenarios

| # | Scenario | `activeEnvironment` value | URL example | Auto-detect runs? | Local bind check? | `localhost` rewrite? |
|---|---|---|---|---|---|---|
| **A** | Local dev on your machine | `development` | `http://localhost:3005` | ✅ Yes | ✅ Yes | ✅ `localhost` → `host.docker.internal` |
| **B** | Coworker's machine on LAN | custom (e.g. `qa-lan`) | `http://192.168.1.50:3005` | ❌ No (remote) | ❌ Skipped | ❌ No (IP stays as-is) |
| **C** | Cloud QA / staging | `staging` | `https://qa.edocs.example.com` | ❌ No (remote) | ❌ Skipped | ❌ No (hostname stays) |
| **D** | Cloud production | `production` | `https://api.edocs.example.com` | ❌ No (remote) | ❌ Skipped | ❌ No (hostname stays) |

All four scenarios share the same `run-all-tests.sh` entry point. The only difference is which environment block in `test-config.json` is active and what values live in it.

### Scenario A: Local dev on your machine

This is the default. See [Quick Start](#quick-start) and [Configuration](#configuration) above.

```bash
./run-all-tests.sh api
./run-all-tests.sh ui feature-file.feature
```

- **Auto-detect** parses `server/.env` and `client/vite.config.ts` (or `next.config.js`, `angular.json`, etc.) to find backend + frontend ports
- **test-config.json** `.environments.development` is kept in sync automatically
- **Pre-flight** verifies each port is bound to `0.0.0.0` (not `127.0.0.1`) and is reachable from the Test MCP container via `host.docker.internal`
- See [SUT_SETUP_GUIDE.md](./SUT_SETUP_GUIDE.md) for framework-specific bind-address commands when the pre-flight fails

### Scenario B: Coworker's machine on the LAN

Your coworker is running the app on their laptop at IP `192.168.1.50`. You want to run tests from your machine against their running app — useful for pair-debugging, cross-platform validation, or verifying a WIP branch before it's pushed.

#### Required setup on the coworker's machine

1. **Bind the SUT to `0.0.0.0`**, not `127.0.0.1`. Same rule as local dev, same framework-specific commands from [SUT_SETUP_GUIDE.md](./SUT_SETUP_GUIDE.md):
   - Vite: `npm run dev -- --host` (or `server.host: '0.0.0.0'` in `vite.config.ts`)
   - Express: `app.listen(3005)` (Node's default is already `0.0.0.0`)
   - Django: `python manage.py runserver 0.0.0.0:8000`
   - etc.

2. **Allow the ports through their OS firewall**:
   - **Windows** (PowerShell as admin):
     ```powershell
     New-NetFirewallRule -DisplayName "Dev Frontend 5173" -Direction Inbound `
       -LocalPort 5173 -Protocol TCP -Action Allow -Profile Private
     New-NetFirewallRule -DisplayName "Dev Backend 3005" -Direction Inbound `
       -LocalPort 3005 -Protocol TCP -Action Allow -Profile Private
     ```
   - **macOS**: System Preferences → Security & Privacy → Firewall → Firewall Options → allow incoming connections for Node/Vite binaries
   - **Linux**: `sudo ufw allow 5173/tcp && sudo ufw allow 3005/tcp`

3. **Give you their LAN IP**: `ipconfig` (Windows) or `ifconfig` / `ip addr` (Mac/Linux). Look for the IPv4 address on the interface connected to your shared network (e.g., `192.168.1.50`).

4. **Add your machine to their frontend's `allowedHosts`** (frameworks that do Host-header validation — Vite, Angular, Webpack-dev-server). Example for Vite's `vite.config.ts`:
   ```typescript
   server: {
     host: '0.0.0.0',
     port: 5173,
     allowedHosts: [
       'localhost',
       '127.0.0.1',
       '192.168.1.50',          // their own IP (for self-test)
       '192.168.1.42',          // your IP (so your tests reach them)
       '.local',                // optionally allow *.local mDNS hostnames
     ],
   }
   ```

5. **Update the backend's CORS origin** (if the backend enforces CORS) in their `server/.env`:
   ```
   CORS_ORIGIN=http://192.168.1.50:5173,http://192.168.1.42:5173
   ```

#### One-time setup on your machine

Add a new environment block to `tests/config/test-config.json`:

```json
{
  "environments": {
    "development": {
      "baseUrl": "http://localhost:5173",
      "apiUrl":  "http://localhost:3005"
    },
    "qa-lan": {
      "baseUrl": "http://192.168.1.50:5173",
      "apiUrl":  "http://192.168.1.50:3005",
      "description": "Coworker's dev box on the LAN"
    }
  },
  "activeEnvironment": "development"
}
```

Pick any name you like — `qa-lan`, `pair-debug`, `jane-local`, etc. The name becomes the `--env` flag value.

#### Running the tests

```bash
./run-all-tests.sh api --env qa-lan
./run-all-tests.sh ui feature-file.feature --env qa-lan
```

What happens:

1. `--env qa-lan` sets `ENV_OVERRIDE=qa-lan` internally
2. **Auto-detect is skipped** (any environment other than `development` skips it) with an explicit warning:
   ```
   [WARN] Auto-detect skipped — targeting remote environment 'qa-lan'
   [WARN]   Remote URLs are read as-is from test-config.json.
   ```
3. `load_test_config` reads `environments.qa-lan.baseUrl` = `http://192.168.1.50:5173`
4. **No localhost rewrite** — the URL has no `localhost` substring, so the sed-based translation is a no-op. The URL stays as `http://192.168.1.50:5173`.
5. `check-sut.sh` sees that `192.168.1.50` is **not** a local hostname (not `localhost` / `127.0.0.1` / `::1` / `host.docker.internal`) and switches to **remote probe mode** — skips the netstat bind check and runs HTTP probes only:
   - **Host-side probe**: `curl -s -I http://192.168.1.50:5173` from your machine. Catches DNS failures, missing VPN, blocked LAN routes.
   - **Container-side probe**: `docker exec projexlight-test-mcp curl -s -I http://192.168.1.50:5173`. Catches Docker egress restrictions (rare — corporate proxy, custom network mode).
6. Tests run. Playwright inside the container navigates to your coworker's IP. API calls from the container reach the coworker's backend directly.

**No `host.docker.internal` involved.** The container's outbound traffic to a LAN IP uses Docker's default bridge → your host's LAN interface → the coworker's machine. That's just normal network routing; no Docker Desktop magic needed.

### Scenario C: Cloud QA / staging

Your QA team has deployed the app to `https://qa.edocs.example.com` (frontend) and `https://api-qa.edocs.example.com` (backend, if separate). You want to run tests against that deployment from your local machine.

#### No cloud-side setup needed

Cloud deployments running in ECS / Cloud Run / App Service / Kubernetes typically already:

- Bind to `0.0.0.0` inside their container (the container runtime handles the rest)
- Sit behind a reverse proxy (CloudFront, nginx, Vercel, Cloudflare) that terminates TLS and normalizes Host headers
- Accept any Host header at the CDN/proxy level (no `allowedHosts` restriction at your level)
- Resolve via public DNS

The only configuration is on your side.

#### One-time test-config.json setup

```json
{
  "environments": {
    "staging": {
      "baseUrl": "https://qa.edocs.example.com",
      "apiUrl":  "https://api-qa.edocs.example.com",
      "description": "Cloud staging deployment for pre-release testing"
    }
  }
}
```

If your frontend and backend are served from the same domain (common for Next.js full-stack apps, or frontend apps that proxy `/api` through their CDN to a backend), use the same URL for both:

```json
"staging": {
  "baseUrl": "https://qa.edocs.example.com",
  "apiUrl":  "https://qa.edocs.example.com"
}
```

#### Running

```bash
./run-all-tests.sh api --env staging
./run-all-tests.sh ui feature-file.feature --env staging
```

Expected output:

```
==============================================
  SUT Port Auto-Detection
==============================================
[WARN] Auto-detect skipped — targeting remote environment 'staging'
[WARN]   Remote URLs are read as-is from test-config.json. To update them,
[WARN]   edit .environments.staging.baseUrl / .apiUrl directly.

==============================================
Loading Test Configuration
==============================================
  Environment (from --env): staging
  UI Base URL: https://qa.edocs.example.com
  API Base URL: https://api-qa.edocs.example.com

==============================================
  SUT Connectivity Pre-Flight
==============================================
  Primary URL: https://api-qa.edocs.example.com

  → Checking https://api-qa.edocs.example.com
    ℹ Remote host — skipping local bind-address check
    ✓ Host-side HTTP probe succeeded (from local machine)
    ✓ Container → api-qa.edocs.example.com probe succeeded

==============================================
  ✓ SUT is ready — safe to run tests
==============================================
```

No false positives about port 443 not listening locally. The runner correctly classifies the URL as remote.

#### Common staging-specific gotchas

| Issue | Symptom | Fix |
|---|---|---|
| VPN required | `curl: (6) Could not resolve host qa.edocs.example.com` | Connect to your corporate VPN, retry |
| Self-signed / untrusted TLS cert | `curl: (60) SSL certificate problem` | For dev/staging only: `NODE_TLS_REJECT_UNAUTHORIZED=0 ./run-all-tests.sh api --env staging`. For production, fix the cert. |
| Corporate HTTP proxy | Container probes time out but host probes work | Add `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` env vars to the Test MCP compose file's `environment:` block |
| Auth required | All tests return 401 | Set auth token — see [Authentication for remote environments](#authentication-for-remote-environments) below |
| Rate limiting | Random 429s under test load | Exempt staging from rate limits, or run tests with `--concurrency 1` |
| Stale browser cache | Tests fail but manual browser works | Playwright launches a fresh browser context per run — this usually isn't the cause, but you can force `--headed` for manual debugging |

### Scenario D: Cloud production (read-only smoke tests)

Same structure as scenario C but pointing at your production URL. **Critical rule**: production tests must be READ-ONLY. No `POST` / `PUT` / `DELETE` / `PATCH` in feature files that execute against production.

#### One-time test-config.json setup

```json
{
  "environments": {
    "production": {
      "baseUrl": "https://edocs.example.com",
      "apiUrl":  "https://api.edocs.example.com",
      "readOnly": true,
      "description": "Production — READ-ONLY smoke tests only"
    }
  }
}
```

The `readOnly: true` field is a convention your test feature files and API test definitions should check before running destructive operations. The runner does not enforce this automatically — it's on you to structure your tests safely.

#### Running

```bash
./run-all-tests.sh api --env production
```

**Recommended practice**: keep production smoke tests in a separate directory (e.g. `tests/features/production-smoke/`) and gate with a feature file argument:

```bash
./run-all-tests.sh ui production-smoke/health-check.feature --env production
```

This way a casual `./run-all-tests.sh ui --env production` can't accidentally exercise a full registration flow against your real users.

### Authentication for remote environments

Remote SUTs typically need an auth token. The runner supports three patterns — pick whichever matches your deployment.

#### Pattern 1 — Bearer token / API key via env var

Most API-only tests. The simplest pattern. Put the token in `mcp-server/.env` (which is git-ignored by default — do not commit):

```bash
# mcp-server/.env
PROJEXLIGHT_STAGING_API_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

Reference it in `test-config.json` via the environment's `apiAuthConfig` block:

```json
{
  "environments": {
    "staging": {
      "baseUrl": "https://qa.edocs.example.com",
      "apiUrl":  "https://api-qa.edocs.example.com",
      "apiAuthType": "bearer",
      "apiAuthConfig": {
        "token": "${PROJEXLIGHT_STAGING_API_TOKEN}"
      }
    }
  }
}
```

`load_test_config` expands the `${VAR}` reference at runtime and exports `API_AUTH_TYPE=bearer` + `API_AUTH_TOKEN=eyJ...`. The test runner attaches `Authorization: Bearer <token>` to every API call automatically.

#### Pattern 2 — UI login flow (BDD / Playwright tests)

UI tests need to log in through the actual login page. Set credentials and login selectors in `test-config.json`:

```json
{
  "testCredentials": {
    "default": {
      "email": "qa-user@example.com",
      "password": "${TEST_USER_PASSWORD}"
    }
  },
  "loginConfig": {
    "loginUrl": "/login",
    "emailField": "email",
    "passwordField": "password",
    "submitButton": "Login",
    "successIndicator": "Dashboard"
  }
}
```

With `${TEST_USER_PASSWORD}` set in `mcp-server/.env` so the password doesn't hit git. The Playwright runner uses these to perform auto-login at the start of each scenario.

For multiple roles:

```json
"testCredentials": {
  "admin": { "email": "admin-qa@example.com", "password": "${ADMIN_PASSWORD}" },
  "user":  { "email": "user-qa@example.com",  "password": "${USER_PASSWORD}"  },
  "readonly": { "email": "readonly-qa@example.com", "password": "${READONLY_PASSWORD}" }
}
```

Feature files reference roles via `Given I am logged in as <admin>`.

#### Pattern 3 — OAuth (Google, Auth0, Okta, etc.)

OAuth is harder because the test can't complete an interactive third-party redirect. Three sub-patterns:

- **Test-only OAuth bypass**: your staging backend exposes a test-only endpoint that issues tokens without going through the real OAuth provider. Only safe behind a staging-specific secret. Configured via `apiAuthType: "oauth2_bypass"` + the bypass endpoint URL.
- **Pre-obtained refresh token**: you generate a refresh token manually once, store it in `mcp-server/.env` as `OAUTH_REFRESH_TOKEN`, and the runner exchanges it for an access token at the start of each run via a token-refresh endpoint configured in `apiAuthConfig.tokenUrl`.
- **Client-credentials flow**: for service-to-service auth, configure `apiAuthType: "oauth2"` with `clientId`, `clientSecret`, `tokenUrl`, `scope`. The runner performs the client-credentials exchange automatically on every test run.

```json
"staging": {
  "apiAuthType": "oauth2",
  "apiAuthConfig": {
    "clientId": "${OAUTH_CLIENT_ID}",
    "clientSecret": "${OAUTH_CLIENT_SECRET}",
    "tokenUrl": "https://auth.example.com/oauth/token",
    "scope": "read:leads write:leads"
  }
}
```

### What behaves differently in remote mode

A few features are local-only and will silently skip or warn when pointed at a remote SUT. None of these block the test run — they're just pre-flight aids that don't apply to a box you don't own.

| Feature | Local dev (A) | Remote (B, C, D) | Why |
|---|---|---|---|
| Auto-detect ports from framework configs | ✅ runs | ❌ skipped with warning | Can't parse `server/.env` on a remote machine |
| Bind-address check (`netstat`) | ✅ runs | ❌ skipped silently | Can't introspect a box you don't control |
| `localhost` → `host.docker.internal` rewrite | ✅ applied | ❌ no-op (URL has no `localhost`) | The SUT is not on the Docker host |
| Host-side HTTP probe | ✅ runs | ✅ runs (more useful here — catches VPN / DNS) | Works for any reachable URL |
| Container-side HTTP probe | ✅ runs | ✅ runs (the real test) | Container has outbound internet by default |
| Framework-specific bind-fix suggestions on failure | ✅ shown | ❌ irrelevant | SUT config is not yours to change |
| `docker exec` into the SUT | N/A — SUT is not in a container | N/A | You can only exec into the Test MCP container |

In remote mode, the most important check is the **container-side HTTP probe** — it tells you whether the Test MCP container can actually reach the URL it'll test against. If that succeeds, your tests will have the same network view as the probe.

### Quick reference — environment setup commands

```bash
# Edit test-config.json to add a new environment (one-time)
jq '.environments["qa-lan"] = {
  "baseUrl": "http://192.168.1.50:5173",
  "apiUrl":  "http://192.168.1.50:3005",
  "description": "Coworker LAN"
}' tests/config/test-config.json > /tmp/tc.json && mv /tmp/tc.json tests/config/test-config.json

# Run API tests against the new environment
./mcp-server/run-all-tests.sh api --env qa-lan

# Run UI tests against staging
./mcp-server/run-all-tests.sh ui login.feature --env staging

# Run against production with the read-only smoke suite
./mcp-server/run-all-tests.sh ui production-smoke/health.feature --env production

# Override URL at runtime without touching test-config.json (ad-hoc)
UI_BASE_URL=http://192.168.1.50:5173 API_BASE_URL=http://192.168.1.50:3005 \
  ./mcp-server/run-all-tests.sh api

# Bypass the pre-flight SUT check in CI where the SUT is known-healthy
SKIP_SUT_CHECK=true ./mcp-server/run-all-tests.sh api --env staging
```

---

## Troubleshooting

### Test MCP container not starting

```bash
# Check Docker status
docker ps

# Pull latest image
docker pull projexlight/projex-test-mcp:latest

# Check container logs
./setup-test-mcp.sh logs
```

### Feature file not found

```bash
# Check tests directory exists
ls -la ../tests/features/
```

### API tests failing

```bash
# Check API is running
curl http://localhost:3020/health

# Run with verbose logging
docker logs projexlight-test-mcp -f
```

### Connection refused errors

```bash
# For Docker-to-host connections, use:
API_BASE_URL=http://host.docker.internal:3020 ./run-all-tests.sh api
```

### ⚠️ Host binding — the #1 cause of `Cannot connect to host.docker.internal:PORT`

**Before you blame Docker, the network, or the compose file — check your SUT's bind address.**

If you see every test failing with the same error:
```
✗ Failed: Cannot connect to host host.docker.internal:3005 ssl:default
  [Connect call failed ('192.168.65.254', 3005)]
```
...it almost always means your web app (the SUT) is **running** but **bound to `127.0.0.1` (loopback only)** instead of `0.0.0.0` (all interfaces). Docker containers reach the host via a virtual network adapter, not through loopback, so a loopback-bound socket refuses all container → host traffic even when `host.docker.internal` resolves correctly.

**Full fix with framework-specific bind commands**: see [`SUT_SETUP_GUIDE.md`](./SUT_SETUP_GUIDE.md) in this directory. That guide covers:

- How to start 12+ backends and frontends bound to `0.0.0.0` — Express, NestJS, Django, Flask/FastAPI, Spring Boot, Go, Rails, Vite, CRA, Next.js, Angular, Nuxt/SvelteKit/Astro
- How to verify with `netstat` / `lsof` / `ss`
- How to verify from inside the Test MCP container with `docker exec ... curl host.docker.internal:PORT`
- Windows Firewall allow rules for Docker Desktop vEthernet
- A troubleshooting flowchart for this exact error

**Automatic pre-flight check**: `run-all-tests.sh` now runs `check-sut.sh` before every test command. If your SUT is not reachable, it aborts with an actionable error message pointing at `SUT_SETUP_GUIDE.md` and prints quick-reference bind commands for common frameworks. To bypass the check in CI where the SUT runs on a different host:

```bash
SKIP_SUT_CHECK=true ./run-all-tests.sh api
```

**Quick-reference bind commands** (full table in `SUT_SETUP_GUIDE.md`):

| Framework | Wrong | Right |
|---|---|---|
| **Express** | `app.listen(3005, 'localhost')` | `app.listen(3005)` |
| **Vite** | `npm run dev` | `npm run dev -- --host` |
| **Next.js** | `next dev` | `next dev -H 0.0.0.0` |
| **Create React App** | `npm start` | `HOST=0.0.0.0 npm start` |
| **Angular** | `ng serve` | `ng serve --host 0.0.0.0 --disable-host-check` |
| **Django** | `python manage.py runserver` | `python manage.py runserver 0.0.0.0:8000` |
| **Flask** | `flask run` | `flask run --host=0.0.0.0` |
| **FastAPI** | `uvicorn main:app` | `uvicorn main:app --host 0.0.0.0` |
| **Spring Boot** | (safe by default) | — |
| **Go** | `http.ListenAndServe("localhost:8080", ...)` | `http.ListenAndServe(":8080", ...)` |
| **Rails** | `rails server` | `rails server -b 0.0.0.0` |
| **Nuxt / SvelteKit / Astro** | `nuxt dev` / `vite dev` / `astro dev` | append `--host 0.0.0.0` |

### Client framework allow-hosts — the #2 cause of test failures

Distinct from the bind-address issue above: once your SUT is bound correctly and receiving requests, some dev-server frameworks **reject** requests whose `Host` header is `host.docker.internal`. This produces a different error:

```
Blocked request. This host ('host.docker.internal') is not allowed.
```

Fix by adding `host.docker.internal` to the framework's allowed-hosts list:

**Vite Configuration** — add `allowedHosts` to `vite.config.ts`:

```typescript
export default defineConfig({
  server: {
    host: '0.0.0.0',
    allowedHosts: ['host.docker.internal', 'localhost', '127.0.0.1'],
    proxy: {
      // your proxy settings...
    }
  }
})
```

**Other frameworks**:

| Framework | Configuration |
|-----------|--------------|
| **Create React App** | Set `DANGEROUSLY_DISABLE_HOST_CHECK=true` in `.env` or configure `allowedHosts` in webpack config |
| **Next.js** | Add `host.docker.internal` to `next.config.js` `experimental.allowedDevOrigins` |
| **Angular** | Use `--disable-host-check` flag or set `"disableHostCheck": true` in `angular.json` `architect.serve.options` |
| **Vue CLI** | Set `disableHostCheck: true` or configure `allowedHosts` in `vue.config.js` |
| **Nuxt** | Set `devServer.host: '0.0.0.0'` in `nuxt.config.ts` (newer versions accept by default) |

**Summary**: the bind-address fix and the allowed-hosts fix are BOTH required for Vite/CRA/Angular/etc. You need the server to accept the connection (bind address) AND to not reject the request body (allowed hosts). `SUT_SETUP_GUIDE.md` documents the bind-address half; this section covers the allowed-hosts half.

---

## REST API Endpoints

The Test MCP provides REST API endpoints for programmatic test execution. Two execution modes are supported:

| Mode | Description | Data Source |
|------|-------------|-------------|
| **Local** | Load test definitions from project files | `tests/features/`, `tests/api_definitions/` |
| **Database** | Fetch test definitions from ProjexLight | ProjexLight API (by feature_id, sprint_id) |

---

### API Testing Only

#### Option 1: Local Mode (from files)

```bash
curl -X POST http://localhost:8000/run-api-test \
  -H "Content-Type: application/json" \
  -d '{
    "tests": [
      {
        "name": "Health Check",
        "method": "GET",
        "url": "/api/health",
        "expectedStatus": 200
      },
      {
        "name": "Login API",
        "method": "POST",
        "url": "/api/auth/login",
        "body": {"email": "test@example.com", "password": "Test@123"},
        "expectedStatus": 200
      }
    ],
    "base_url": "http://host.docker.internal:3020"
  }'
```

#### Option 2: Database Mode (from ProjexLight)

Fetch API definitions from ProjexLight database by feature_id or sprint_id:

```bash
curl -X POST http://localhost:8000/run-api-test-by-feature \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "database",
    "feature_id": "fb9c4d72-80b3-483d-ba63-7c71ea2fc46d",
    "projexlight_api_url": "https://api.projexlight.com",
    "projexlight_api_key": "your_api_key",
    "project_id": "709c4270",
    "environment": "development",
    "generate_report": true
  }'
```

#### Option 3: Local Files Mode (auto-load from folder)

```bash
curl -X POST http://localhost:8000/run-api-test-by-feature \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "local",
    "local_path": "/workspace/tests/api_definitions",
    "environment": "development",
    "generate_report": true
  }'
```

---

### UI Testing Only

#### Option 1: Run Single Feature (inline content)

```bash
curl -X POST http://localhost:8000/run-feature \
  -H "Content-Type: application/json" \
  -d '{
    "feature_content": "Feature: Login\n  Scenario: Valid login\n    Given I navigate to \"/login\"\n    When I fill \"email\" with \"test@example.com\"\n    And I click \"Login\"\n    Then I should see \"Dashboard\"",
    "baseUrl": "http://host.docker.internal:3000"
  }'
```

#### Option 2: Run Feature File (from path)

```bash
curl -X POST http://localhost:8000/run-feature \
  -H "Content-Type: application/json" \
  -d '{
    "featureFile": "/workspace/tests/features/login.feature",
    "baseUrl": "http://host.docker.internal:3000",
    "options": {
      "headless": true,
      "screenshot": true
    }
  }'
```

---

### Unified Testing (UI + API Together)

Run both UI and API tests for a feature with a combined report.

#### Option 1: Database Mode (recommended for CI/CD)

```bash
curl -X POST http://localhost:8000/run-unified-test \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "database",
    "feature_id": "fb9c4d72-80b3-483d-ba63-7c71ea2fc46d",
    "run_ui_tests": true,
    "run_api_tests": true,
    "projexlight_api_url": "https://api.projexlight.com",
    "projexlight_api_key": "your_api_key",
    "project_id": "709c4270",
    "environment": "development",
    "generate_report": true,
    "save_results": true
  }'
```

#### Option 2: Local Mode (from project files)

```bash
curl -X POST http://localhost:8000/run-unified-test \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "local",
    "run_ui_tests": true,
    "run_api_tests": true,
    "local_features_path": "/workspace/tests/features",
    "local_api_path": "/workspace/tests/api_definitions",
    "ui_base_url": "http://host.docker.internal:3000",
    "api_base_url": "http://host.docker.internal:3020",
    "generate_report": true
  }'
```

#### Option 3: API Only (skip UI tests)

```bash
curl -X POST http://localhost:8000/run-unified-test \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "database",
    "feature_id": "fb9c4d72-80b3-483d-ba63-7c71ea2fc46d",
    "run_ui_tests": false,
    "run_api_tests": true,
    "projexlight_api_url": "https://api.projexlight.com",
    "projexlight_api_key": "your_api_key",
    "project_id": "709c4270"
  }'
```

#### Option 4: UI Only (skip API tests)

```bash
curl -X POST http://localhost:8000/run-unified-test \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "database",
    "feature_id": "fb9c4d72-80b3-483d-ba63-7c71ea2fc46d",
    "run_ui_tests": true,
    "run_api_tests": false,
    "projexlight_api_url": "https://api.projexlight.com",
    "projexlight_api_key": "your_api_key",
    "project_id": "709c4270"
  }'
```

---

### API Orchestration (Dependent Tests)

Run API tests with dependencies and variable chaining.

#### Using Pre-defined Chain (from database)

```bash
curl -X POST http://localhost:8000/run-orchestration \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": "auth-order-payment-chain",
    "projexlight_api_url": "https://api.projexlight.com",
    "projexlight_api_key": "your_api_key",
    "project_id": "709c4270",
    "environment": "development",
    "variables": {
      "test_email": "user@test.com"
    }
  }'
```

#### Using Auto-Orchestration (for an endpoint)

```bash
curl -X POST http://localhost:8000/run-orchestration \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint_id": "create-order-endpoint-id",
    "projexlight_api_url": "https://api.projexlight.com",
    "projexlight_api_key": "your_api_key",
    "project_id": "709c4270",
    "run_cleanup": true
  }'
```

#### Inline Orchestration Config

```bash
curl -X POST http://localhost:8000/run-orchestration \
  -H "Content-Type: application/json" \
  -d '{
    "orchestration_config": {
      "name": "Order Flow",
      "steps": [
        {
          "name": "Login",
          "url": "/api/auth/login",
          "method": "POST",
          "body": {"email": "test@example.com", "password": "Test@123"},
          "extract": {"token": "$.token"}
        },
        {
          "name": "Create Order",
          "url": "/api/orders",
          "method": "POST",
          "headers": {"Authorization": "Bearer {{token}}"},
          "body": {"product_id": "123", "quantity": 2},
          "extract": {"order_id": "$.id"}
        }
      ]
    },
    "variables": {"base_url": "http://host.docker.internal:3020"}
  }'
```

---

### Test Data Sets (Multiple Variations)

API definitions can include multiple test variations (positive, negative, edge cases):

```json
{
  "name": "Login API",
  "endpoint": "/api/auth/login",
  "method": "POST",
  "testDataSets": [
    {
      "id": "positive-valid",
      "name": "Valid credentials",
      "test_type": "positive",
      "is_active": true,
      "variables": {"email": "user@test.com", "password": "correct123"},
      "expected_response": {"status": 200}
    },
    {
      "id": "negative-wrong-pass",
      "name": "Wrong password",
      "test_type": "negative",
      "is_active": true,
      "variables": {"email": "user@test.com", "password": "wrong"},
      "expected_response": {"status": 401}
    },
    {
      "id": "negative-empty-email",
      "name": "Empty email",
      "test_type": "negative",
      "variables": {"email": "", "password": "test"},
      "expected_response": {"status": 422}
    }
  ]
}
```

Each active `testDataSet` runs as a separate test with its own expected status.

---

### Response Format

All endpoints return JSON with:

```json
{
  "status": "completed",
  "results": [
    {
      "name": "Login API - Valid credentials",
      "status": "passed",
      "status_code": 200,
      "response_time": 124,
      "curl_command": "curl -X POST http://localhost:3020/api/auth/login -H 'Content-Type: application/json' -d '{...}'"
    }
  ],
  "summary": {
    "total": 10,
    "passed": 9,
    "failed": 1,
    "pass_rate": 90.0
  },
  "html_report": "<!DOCTYPE html>..."
}
```

---

### Endpoint Reference

| Endpoint | Purpose | Modes |
|----------|---------|-------|
| `POST /run-api-test` | Run inline API tests | Local only |
| `POST /run-api-test-by-feature` | Run API tests by feature | Local, Database |
| `POST /run-feature` | Run UI feature tests | Local only |
| `POST /run-unified-test` | Run UI + API tests | Local, Database |
| `POST /run-orchestration` | Run dependent API chain | Database, Inline |
| `POST /run-api-workflow` | Run API workflow steps | Inline config |
| `GET /health` | Health check | - |

---

## Feature File Format

Feature files follow Gherkin syntax with ProjexLight metadata:

```gherkin
@feature_id:0d9a7b15-2c75-4ac4-8ade-bbc8dd9e4e75
@epic_id:aa89024e-7a11-48a1-93f6-b3871c09f7cf
Feature: Lead & Contact Management
  Core functionality to manage leads and contacts.

  @scenario_id:8aa0cc8f-c800-480f-bb8a-4bc73296360f
  @scenario_type:Integration
  Scenario: Search for leads
    Given A user is logged into the CRM
    When The user searches for "John"
    Then The system displays matching leads
```

### Supported Tags

- `@feature_id:<uuid>` - Unique feature identifier
- `@scenario_id:<uuid>` - Unique scenario identifier
- `@scenario_type:<type>` - Integration, UI, API
- `@ui_test` - UI automation test
- `@api_test` - API test

---

## API Definition Format

```json
{
  "endpoint": "/api/auth/login",
  "method": "POST",
  "description": "Login with email and password",
  "category": "auth",
  "requiresAuth": false,
  "testCases": [
    {
      "name": "Login with valid credentials",
      "priority": 1,
      "payload": {
        "email": "{{cache:user.email}}",
        "password": "{{static:SecurePass123!}}"
      },
      "expectedStatus": 200
    },
    {
      "name": "Login with invalid password",
      "priority": 2,
      "payload": {
        "email": "{{static:test@example.com}}",
        "password": "{{static:wrong}}"
      },
      "expectedStatus": 401
    }
  ]
}
```

---

## Support

For issues or questions:
- Check container logs: `./setup-test-mcp.sh logs`

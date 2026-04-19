# ProjexLight Code Generation

## STEP 0: VERIFY CURL IS INSTALLED (MANDATORY)

Before proceeding, you MUST verify curl is available. Run this check:

```bash
curl --version
```

### If curl is NOT installed:

**Windows (run in PowerShell as Admin):**
```powershell
winget install curl.curl
```
Or download from: https://curl.se/windows/

**macOS:**
```bash
brew install curl
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get update && sudo apt-get install -y curl
```

**Linux (CentOS/RHEL/Fedora):**
```bash
sudo yum install -y curl
```

**Alpine Linux:**
```bash
apk add curl
```

### ⚠️ STOP IF CURL INSTALLATION FAILS

If curl cannot be installed automatically:
1. **STOP code generation immediately**
2. **Tell the user:** "curl is required but not installed. Please install curl manually and restart."
3. **DO NOT proceed** until curl is confirmed working

### Verify curl works:
```bash
curl --version
```
If this shows version info, proceed to Step 1.

---

## STEP 1: DETECT PROJECT PATH

First, get your project path in Unix format:

**Windows:** Convert `C:\Users\name\project` to `/c/Users/name/project`
**Mac/Linux:** Use path as-is (e.g., `/Users/name/project`)

---

## STEP 1.5: VERIFY CODEBASE (LEGACY EXTRACTION ONLY)

Read `.projexlight/context/framework-config.json`. If it contains a `repository_url` field, this is a legacy extraction project that needs the source codebase.

**Check if source code exists in project root:**
- Look for source files: package.json, requirements.txt, pom.xml, go.mod, Cargo.toml, etc.
- Look for source directories: src/, lib/, app/, server/, client/

**If source code is NOT present and `repository_url` exists:**
```bash
git clone <repository_url> temp_clone && cp -r temp_clone/* . && cp -r temp_clone/.* . 2>/dev/null; rm -rf temp_clone
```
If cloning fails (private repo), ask the user to clone manually into this directory.

**If source code already exists:** Skip cloning and proceed.

---

## STEP 2: READ PROJECT CONFIGURATION

```
.projexlight/context/framework-config.json
```

This tells you the language/framework. If all values are null, this is likely a legacy extraction project.

---

## STEP 3: GET COMPLETE WORKFLOW

**IMPORTANT:** Pass your projectPath to get the correct workflow (auto-detects legacy vs new development):

```bash
curl "http://localhost:8766/api/instruction/bootstrap?projectPath=<YOUR_PROJECT_PATH>"
```

Example:
```bash
curl "http://localhost:8766/api/instruction/bootstrap?projectPath=/c/Users/name/myproject"
```

This returns the complete EXECUTION_FLOW with all phases and MCP tools.
- **Legacy Extraction projects:** Returns legacy API extraction workflow with extraction tools
- **New Development projects:** Returns standard development workflow

---

## STEP 3.5: PREFER MCP TOOLS OVER CURL (MANDATORY)

**After the initial bootstrap curl call above, you MUST prefer MCP tools for ALL subsequent API calls.**

### How to detect MCP tools:
1. Check if `projexlight_*` MCP tools are available in your tool list (e.g., `projexlight_init_session`, `projexlight_get_instruction`, `projexlight_complete_task`, etc.)
2. If MCP tools are configured and available — **use them directly** instead of curl for all API interactions

### Tool preference order:
1. **✅ FIRST:** Use the matching `projexlight_*` MCP tool if available (e.g., use `projexlight_init_session` instead of `curl POST /api/mcp/instruction/init`)
2. **⚠️ FALLBACK:** Only use curl if NO matching MCP tool exists for a specific API endpoint

### Why MCP tools are preferred:
- MCP tools handle authentication, project context, and error handling automatically
- No need to manually pass sessionToken, projectId, or projectPath — the MCP bridge injects these
- Faster and more reliable than raw curl calls
- Tool responses are structured and easier to parse

### Common MCP tool mappings:
| Instead of curl to... | Use MCP tool |
|---|---|
| `POST /api/mcp/instruction/init` | `projexlight_init_session` |
| `POST /api/mcp/instruction/get` | `projexlight_get_instruction` |
| `POST /api/mcp/instruction/validate` | `projexlight_validate` |
| `POST /api/mcp/instruction/complete` | `projexlight_complete_task` |
| `GET /api/mcp/instruction/rules` | `projexlight_get_rules` |
| `POST /api/mcp/test/start` | `projexlight_start_api_tests` |
| `GET /api/mcp/test/status` | `projexlight_get_api_test_status` |
| Any other `/api/mcp/*` endpoint | Check for matching `projexlight_*` tool |

**IMPORTANT:** The initial bootstrap call (Step 3) MUST always use curl since MCP tools may not yet be initialized. All subsequent calls should use MCP tools when available.

---

## STEP 4: FOLLOW EXECUTION_FLOW FROM RESPONSE

Execute each phase automatically. DO NOT ask for confirmation. Use MCP tools (not curl) for each step when available.

The response includes:
- **PROJECT_TYPE**: "legacy_extraction" or "new_development"
- **MCP_TOOLS**: Available tools for this project type
- **EXECUTION_FLOW**: Step-by-step workflow to follow
- **NEXT_STEP**: First action to take

---

## CRITICAL RULES

- Pass projectPath in bootstrap call to detect correct workflow
- Read framework-config.json to know language/framework
- **Use MCP tools (`projexlight_*`) for all API calls after the initial bootstrap curl** — only fall back to curl if no matching tool exists
- Execute all steps automatically - DO NOT ask for confirmation
- Follow NEXT_STEP in each API response
- Validate before commit - only commit when validation.passed === true
- If any curl command fails, check MCP server: `docker ps | grep projexlight`

---

Project: 6f3a50a3... | Tasks: 16

## Notes

Instructions delivered via MCP server.

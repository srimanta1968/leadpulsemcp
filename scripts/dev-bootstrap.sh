#!/usr/bin/env bash
# Dev helper: call POST /api/v1/bootstrap on a locally-running MCP with
# reasonable defaults. After this the MCP connects to Mongo and starts its
# four agent loops (which will poll the fake CRM at LEADPULSE_URL).
set -euo pipefail

MCP_URL="${MCP_URL:-http://127.0.0.1:8000}"
BOOTSTRAP_SECRET="${BOOTSTRAP_SECRET:-dev-secret}"
MONGODB_URL="${MONGODB_URL:-mongodb://admin:password@localhost:27017/?authSource=admin}"
MONGODB_DB="${MONGODB_DB:-leadpulse_mcp_dev}"
LEADPULSE_URL="${LEADPULSE_URL:-http://127.0.0.1:9000}"
LEADPULSE_TOKEN="${LEADPULSE_TOKEN:-dev-token-at-least-16-chars}"
INSTANCE_ID="${INSTANCE_ID:-dev-container-$$}"

echo "Bootstrapping ${MCP_URL}"
curl -fsS -X POST "${MCP_URL}/api/v1/bootstrap" \
  -H "Content-Type: application/json" \
  -H "X-Bootstrap-Secret: ${BOOTSTRAP_SECRET}" \
  -d "$(cat <<EOF
{
  "mongodb_url": "${MONGODB_URL}",
  "mongodb_db": "${MONGODB_DB}",
  "leadpulse_url": "${LEADPULSE_URL}",
  "leadpulse_token": "${LEADPULSE_TOKEN}",
  "instance_id": "${INSTANCE_ID}",
  "sender_agents_per_container": 2
}
EOF
)" | jq .

echo ""
echo "Done. Check:"
echo "  curl ${MCP_URL}/health        # should show configured:true"
echo "  curl ${MCP_URL}/api/v1/bootstrap/status"

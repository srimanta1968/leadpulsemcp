#!/usr/bin/env bash
# Deploy the LeadPulse MCP to AWS ECS Fargate.
#
# This script is invoked BY THE LEADPULSE CRM ADMIN PORTAL (projex_crm) when an
# operator clicks "Deploy MCP version X" in the fleet dashboard. It expects:
#
#   AWS_REGION            — e.g. us-east-1
#   AWS_ACCOUNT_ID        — 12-digit account id
#   DOCKERHUB_USERNAME    — namespace that holds the image
#   IMAGE_TAG             — version to deploy (git short sha); default "latest"
#   ECS_CLUSTER           — target cluster (default "leadpulse-mcp")
#   ECS_SERVICE           — target service (default "leadpulse-mcp-worker")
#   DESIRED_COUNT         — optional, sets service desired-count after deploy
#
# Immediately after the new task enters RUNNING state, the CRM will call
# POST /api/v1/bootstrap on the new task's private IP with the mongo URL,
# LeadPulse URL, and bearer token. This script does NOT pass those secrets
# through the task definition — runtime config is injected at runtime.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"

: "${AWS_REGION:?AWS_REGION is required}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID is required}"
: "${DOCKERHUB_USERNAME:?DOCKERHUB_USERNAME is required}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ECS_CLUSTER="${ECS_CLUSTER:-leadpulse-mcp}"
ECS_SERVICE="${ECS_SERVICE:-leadpulse-mcp-worker}"

TEMPLATE="${ROOT}/aws/task-definition.json"
RENDERED="$(mktemp)"
trap 'rm -f "${RENDERED}"' EXIT

# Simple env-var substitution so we don't need additional tooling.
sed \
  -e "s|\${AWS_ACCOUNT_ID}|${AWS_ACCOUNT_ID}|g" \
  -e "s|\${AWS_REGION}|${AWS_REGION}|g" \
  -e "s|\${DOCKERHUB_USERNAME}|${DOCKERHUB_USERNAME}|g" \
  -e "s|\${IMAGE_TAG}|${IMAGE_TAG}|g" \
  "${TEMPLATE}" > "${RENDERED}"

echo "[1/3] Registering new task definition revision"
TASK_DEF_ARN="$(aws ecs register-task-definition \
  --region "${AWS_REGION}" \
  --cli-input-json "file://${RENDERED}" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)"
echo "  registered: ${TASK_DEF_ARN}"

echo "[2/3] Updating service ${ECS_SERVICE} on cluster ${ECS_CLUSTER}"
UPDATE_ARGS=(
  --region "${AWS_REGION}"
  --cluster "${ECS_CLUSTER}"
  --service "${ECS_SERVICE}"
  --task-definition "${TASK_DEF_ARN}"
  --force-new-deployment
)
if [[ -n "${DESIRED_COUNT:-}" ]]; then
  UPDATE_ARGS+=(--desired-count "${DESIRED_COUNT}")
fi
aws ecs update-service "${UPDATE_ARGS[@]}" >/dev/null
echo "  update-service issued"

echo "[3/3] Waiting for service to stabilize"
aws ecs wait services-stable \
  --region "${AWS_REGION}" \
  --cluster "${ECS_CLUSTER}" \
  --services "${ECS_SERVICE}"

echo "Done. Deployed ${DOCKERHUB_USERNAME}/projex-leadpulse-mcp:${IMAGE_TAG} to ${ECS_CLUSTER}/${ECS_SERVICE}."
echo "LeadPulse CRM should now call POST /api/v1/bootstrap on each task IP."

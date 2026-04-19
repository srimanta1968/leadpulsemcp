#!/usr/bin/env bash
# Build the LeadPulse MCP docker image (Nuitka-compiled) and push to DockerHub.
#
# Required env:
#   DOCKERHUB_USERNAME   — e.g. "projexlight"
#   DOCKERHUB_TOKEN      — a DockerHub access token (NOT the account password)
# Optional env:
#   IMAGE_NAME           — default "leadpulse-mcp"
#   IMAGE_TAG            — default derived from `git rev-parse --short HEAD` or "dev"
#   PLATFORMS            — default "linux/amd64" (for ECS Fargate)
#   PUSH_LATEST          — default "true"; also tags + pushes :latest
#
# Usage:
#   export DOCKERHUB_USERNAME=projexlight
#   export DOCKERHUB_TOKEN=dckr_pat_xxx
#   ./scripts/build-and-push.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"

: "${DOCKERHUB_USERNAME:?DOCKERHUB_USERNAME is required}"
: "${DOCKERHUB_TOKEN:?DOCKERHUB_TOKEN is required}"
IMAGE_NAME="${IMAGE_NAME:-projex-leadpulse-mcp}"
PLATFORMS="${PLATFORMS:-linux/amd64}"
PUSH_LATEST="${PUSH_LATEST:-true}"

cd "${ROOT}"

if [[ -z "${IMAGE_TAG:-}" ]]; then
  if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
  else
    IMAGE_TAG="dev-$(date -u +%Y%m%d%H%M%S)"
  fi
fi

FULL_IMAGE="${DOCKERHUB_USERNAME}/${IMAGE_NAME}:${IMAGE_TAG}"
LATEST_IMAGE="${DOCKERHUB_USERNAME}/${IMAGE_NAME}:latest"

echo "[1/4] Logging in to DockerHub as ${DOCKERHUB_USERNAME}"
echo "${DOCKERHUB_TOKEN}" | docker login -u "${DOCKERHUB_USERNAME}" --password-stdin

echo "[2/4] Ensuring buildx builder exists"
if ! docker buildx inspect leadpulse-builder >/dev/null 2>&1; then
  docker buildx create --name leadpulse-builder --use
else
  docker buildx use leadpulse-builder
fi
docker buildx inspect --bootstrap

BUILD_TAGS=(-t "${FULL_IMAGE}")
if [[ "${PUSH_LATEST}" == "true" ]]; then
  BUILD_TAGS+=(-t "${LATEST_IMAGE}")
fi

echo "[3/4] Building ${FULL_IMAGE} for ${PLATFORMS}"
docker buildx build \
  --platform "${PLATFORMS}" \
  --file Dockerfile \
  "${BUILD_TAGS[@]}" \
  --push \
  .

echo "[4/4] Pushed:"
echo "  - ${FULL_IMAGE}"
if [[ "${PUSH_LATEST}" == "true" ]]; then
  echo "  - ${LATEST_IMAGE}"
fi

echo "Done. Image digest:"
docker buildx imagetools inspect "${FULL_IMAGE}" | head -n 5

#!/usr/bin/env bash
# Smoke test: build the Docker image, run the container, verify /health responds.
# Used by CI after the Nuitka build stage to catch missing-module traps that
# Nuitka's dynamic-import handling can introduce.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
IMAGE="${IMAGE:-projex-leadpulse-mcp:smoke-$(date -u +%Y%m%d%H%M%S)}"
PORT="${PORT:-18000}"
CONTAINER_NAME="projex-leadpulse-mcp-smoke-$$"

cleanup() {
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cd "${ROOT}"

echo "[1/4] Building image ${IMAGE}"
docker build -t "${IMAGE}" -f Dockerfile .

echo "[2/4] Starting container"
docker run --rm -d \
  --name "${CONTAINER_NAME}" \
  -p "${PORT}:8000" \
  -e BOOTSTRAP_SECRET=smoke-secret \
  -e MEM_LIMIT_MB=1024 \
  "${IMAGE}"

echo "[3/4] Waiting for /health"
deadline=$(( $(date +%s) + 40 ))
while [[ $(date +%s) -lt ${deadline} ]]; do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "FAIL: /health never became reachable"
  docker logs "${CONTAINER_NAME}" | tail -80
  exit 1
fi

body="$(curl -sS "http://127.0.0.1:${PORT}/health")"
echo "Health body: ${body}"
echo "${body}" | grep -q '"configured":false' || { echo "FAIL: expected configured:false before bootstrap"; exit 1; }

echo "[4/5] Smoke test passed."

echo "[5/5] Security scan (trivy)"
if command -v trivy >/dev/null 2>&1; then
  trivy image --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed "${IMAGE}"
else
  echo "  trivy not installed; skipping. CI MUST install trivy to block HIGH/CRITICAL CVEs."
fi

echo "Done."

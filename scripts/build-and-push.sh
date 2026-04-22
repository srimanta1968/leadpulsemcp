#!/usr/bin/env bash
# Build the LeadPulse MCP docker image (Nuitka-compiled) and optionally
# push to DockerHub. Mirrors the flag/env conventions used by
# projex_dev_mcp/scripts/build_full.sh so one muscle memory works for
# both repos. Image name is the only intentional difference.
#
# Flags:
#   --fresh, --no-cache        Build with --no-cache
#   --bump major|minor|patch   Increment VERSION file before tagging
#   --push                     Tag + push to DockerHub after build
#
# Env (optional, loaded from scripts/build.env if present):
#   DOCKER_HUB_USERNAME        default "projexlight"
#   DOCKER_HUB_IMAGE_NAME      default "projex-leadpulse-mcp"
#   DOCKERHUB_TOKEN            if set, script runs `docker login` inline;
#                              otherwise relies on a pre-existing session
#   PLATFORMS                  default "linux/amd64" (for ECS Fargate)
#
# Typical usage:
#   ./scripts/build-and-push.sh                      # local build only
#   ./scripts/build-and-push.sh --push               # build + push current VERSION
#   ./scripts/build-and-push.sh --bump patch --push  # bump VERSION then push
#   ./scripts/build-and-push.sh --fresh --push       # clean rebuild + push

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"

# Local image name used during the build (single tag to save space).
IMAGE_NAME="leadpulse-mcp"

# Load build.env for Docker Hub settings (same pattern as build_full.sh)
BUILD_ENV_FILE="$HERE/build.env"
if [ -f "$BUILD_ENV_FILE" ]; then
    source "$BUILD_ENV_FILE"
fi

# Docker Hub settings (only used with --push)
DOCKER_HUB_USERNAME="${DOCKER_HUB_USERNAME:-projexlight}"
DOCKER_HUB_IMAGE_NAME="${DOCKER_HUB_IMAGE_NAME:-projex-leadpulse-mcp}"
DOCKER_HUB_IMAGE="${DOCKER_HUB_USERNAME}/${DOCKER_HUB_IMAGE_NAME}"
PLATFORMS="${PLATFORMS:-linux/amd64}"

# Read version from VERSION file (used only for --push)
VERSION_FILE="$ROOT/VERSION"
if [ -f "$VERSION_FILE" ]; then
    VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
else
    VERSION="1.0.0"
    echo "$VERSION" > "$VERSION_FILE"
fi

# Flags
FRESH_BUILD=false
BUMP_VERSION=""
PUSH_TO_HUB=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fresh|--no-cache)
            FRESH_BUILD=true
            shift
            ;;
        --bump)
            BUMP_VERSION="$2"
            shift 2
            ;;
        --push)
            PUSH_TO_HUB=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# Bump version if requested (only matters for --push)
if [[ -n "$BUMP_VERSION" ]]; then
    IFS='.' read -r major minor patch <<< "$VERSION"
    case "$BUMP_VERSION" in
        major)
            major=$((major + 1))
            minor=0
            patch=0
            ;;
        minor)
            minor=$((minor + 1))
            patch=0
            ;;
        patch)
            patch=$((patch + 1))
            ;;
        *)
            echo "Unknown --bump value: $BUMP_VERSION (expected major|minor|patch)" >&2
            exit 1
            ;;
    esac
    VERSION="${major}.${minor}.${patch}"
    echo "$VERSION" > "$VERSION_FILE"
    echo "Version bumped to: $VERSION"
fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}[ERROR]${NC} Docker is required!"
    exit 1
fi
echo -e "${GREEN}[OK]${NC} Docker found"

if ! docker info &> /dev/null; then
    echo -e "${RED}[ERROR]${NC} Docker is not running!"
    exit 1
fi
echo -e "${GREEN}[OK]${NC} Docker daemon running"

export DOCKER_BUILDKIT=1
echo -e "${GREEN}[OK]${NC} Docker BuildKit enabled"
echo ""

cd "${ROOT}"

#===============================================================================
# Build
#===============================================================================
echo -e "${BLUE}[BUILD]${NC} ${IMAGE_NAME}:latest (platform ${PLATFORMS})"

BUILD_ARGS=(--platform "${PLATFORMS}" -f Dockerfile -t "${IMAGE_NAME}:latest")
if [ "$FRESH_BUILD" = true ]; then
    echo -e "${YELLOW}[INFO]${NC} Fresh build — no cache"
    BUILD_ARGS+=(--no-cache)
else
    echo -e "${GREEN}[INFO]${NC} Incremental build — BuildKit cache"
fi

docker build "${BUILD_ARGS[@]}" .

if ! docker image inspect "${IMAGE_NAME}:latest" > /dev/null 2>&1; then
    echo -e "${RED}[ERROR]${NC} Build failed — image not created"
    exit 1
fi
echo -e "${GREEN}[OK]${NC} Build complete: ${IMAGE_NAME}:latest"
echo ""

#===============================================================================
# Push to Docker Hub (if --push flag)
#===============================================================================
if [ "$PUSH_TO_HUB" = true ]; then
    echo -e "${BLUE}[PUSH]${NC} Pushing to Docker Hub..."

    # If DOCKERHUB_TOKEN is provided, log in inline; otherwise assume an
    # existing `docker login` session.
    if [ -n "${DOCKERHUB_TOKEN:-}" ]; then
        echo "${DOCKERHUB_TOKEN}" | docker login -u "${DOCKER_HUB_USERNAME}" --password-stdin
    fi

    # Tag the local build with version + latest on the DockerHub namespace
    docker tag "${IMAGE_NAME}:latest" "${DOCKER_HUB_IMAGE}:${VERSION}"
    docker tag "${IMAGE_NAME}:latest" "${DOCKER_HUB_IMAGE}:latest"

    if ! docker info 2>/dev/null | grep -q "Username"; then
        echo -e "${YELLOW}[WARN]${NC} Not logged in to Docker Hub"
        echo "  Run: docker login   OR   export DOCKERHUB_TOKEN=dckr_pat_..."
        exit 1
    fi

    docker push "${DOCKER_HUB_IMAGE}:${VERSION}"
    docker push "${DOCKER_HUB_IMAGE}:latest"
    echo -e "${GREEN}[OK]${NC} Pushed: ${DOCKER_HUB_IMAGE}:${VERSION}"
    echo -e "${GREEN}[OK]${NC} Pushed: ${DOCKER_HUB_IMAGE}:latest"
fi

echo ""
echo "================================================================================"
echo -e "${GREEN}SUCCESS${NC} - LeadPulse MCP image ready"
echo "================================================================================"
echo "Local image:     ${IMAGE_NAME}:latest"
echo "VERSION:         ${VERSION}"
if [ "$PUSH_TO_HUB" = true ]; then
    echo "Docker Hub:      ${DOCKER_HUB_IMAGE}:${VERSION} (and :latest)"
else
    echo "Docker Hub:      (skipped — pass --push to upload)"
fi
echo ""
echo "To push later:"
echo "  ./scripts/build-and-push.sh --push"
echo "To bump and push:"
echo "  ./scripts/build-and-push.sh --bump patch --push"
echo "================================================================================"

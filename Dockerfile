# syntax=docker/dockerfile:1.6
# Multi-stage build for the LeadPulse MCP agent.
#
# Stage 1: compile the FastAPI app into a standalone binary with Nuitka.
# Stage 2: minimal runtime image that ships only the compiled binary and
#          the system libs it links against.

# -------------------- build --------------------
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    NUITKA_CACHE_DIR=/nuitka-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        patchelf \
        ccache \
        gcc \
        g++ \
        libffi-dev \
        libssl-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY server/requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
 && pip install -r requirements.txt \
 && pip install "nuitka>=2.0" "ordered-set>=4.1" "zstandard>=0.22"

COPY server/app ./app

# Nuitka compile — standalone single-file binary.
# --onefile produces one executable that unpacks to /tmp on first run.
# --standalone bundles all python deps (no python interpreter needed at runtime).
# --include-package picks up submodules FastAPI discovers dynamically.
RUN python -m nuitka \
        --standalone \
        --onefile \
        --assume-yes-for-downloads \
        --output-dir=/build/out \
        --output-filename=leadpulse-mcp \
        --include-package=app \
        --include-package=fastapi \
        --include-package=uvicorn \
        --include-package=starlette \
        --include-package=pydantic \
        --include-package=pydantic_core \
        --include-package=email_validator \
        --include-package=motor \
        --include-package=pymongo \
        --include-package=bson \
        --include-package=passlib \
        --include-package=jose \
        --include-package=httpx \
        --include-package=openpyxl \
        --include-package=boto3 \
        --include-package=botocore \
        --include-module=uvicorn.lifespan.on \
        --include-module=uvicorn.loops.auto \
        --include-module=uvicorn.protocols.http.auto \
        --include-module=uvicorn.protocols.websockets.auto \
        --python-flag=-OO \
        --remove-output \
        --nofollow-import-to=pytest \
        --nofollow-import-to=tests \
        app/entrypoint.py \
 && ls -lh /build/out/leadpulse-mcp \
 && /build/out/leadpulse-mcp --help >/dev/null 2>&1 || true
# NOTE: Full container smoke test runs post-build via scripts/smoke-test.sh
# (starts the container and polls /health with a 40s deadline). This detects
# missing-module traps that Nuitka's dynamic-import handling can create without
# needing a live Mongo + CRM for the compile stage.

# -------------------- runtime --------------------
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libffi8 \
        libssl3 \
        zlib1g \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 mcp \
    && useradd --uid 10001 --gid mcp --no-create-home --shell /sbin/nologin mcp

WORKDIR /app

COPY --from=builder --chown=mcp:mcp /build/out/leadpulse-mcp /app/leadpulse-mcp
RUN chmod +x /app/leadpulse-mcp

USER mcp

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# tini handles PID 1 reaping (ECS sends SIGTERM on shrink; see design §8).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/leadpulse-mcp"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

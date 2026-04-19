"""Standalone entrypoint used by Nuitka-compiled binary.

Runs uvicorn programmatically so the compiled binary does not depend on a
``python -m uvicorn`` command line being available at runtime.
"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=1,
        log_level=os.environ.get("LOG_LEVEL", "info"),
        access_log=True,
    )


if __name__ == "__main__":
    main()

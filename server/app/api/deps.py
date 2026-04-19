from fastapi import Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.hmac_signing import any_secret_matches
from app.core.runtime_config import RuntimeConfig, runtime_config
from app.db.mongodb import get_db


def require_runtime_config() -> RuntimeConfig:
    if not runtime_config.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP is not bootstrapped. Awaiting configuration from LeadPulse CRM.",
        )
    return runtime_config.get()


def require_db(_: RuntimeConfig = Depends(require_runtime_config)) -> AsyncIOMotorDatabase:
    return get_db()


async def verify_crm_hmac(request: Request) -> None:
    """Verify an inbound CRM call. Returns nothing; raises 401 on failure."""
    cfg = require_runtime_config()
    secrets = [s for s in (cfg.hmac_secret, cfg.hmac_secret_previous) if s]
    if not secrets:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HMAC secret not yet issued by CRM (register pending).",
        )
    body = await request.body()
    ok = any_secret_matches(
        secrets,
        method=request.method,
        path=request.url.path,
        body=body,
        timestamp_header=request.headers.get("X-MCP-Timestamp"),
        signature_header=request.headers.get("X-MCP-Signature"),
        nonce_header=request.headers.get("X-MCP-Nonce"),
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid HMAC signature")

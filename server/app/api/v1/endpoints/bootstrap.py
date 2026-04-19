from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import settings
from app.core.runtime_config import RuntimeConfig, runtime_config
from app.db.mongodb import close_mongo_connection, connect_to_mongo
from app.schemas.bootstrap import BootstrapRequest, BootstrapResponse

router = APIRouter()


@router.post("", response_model=BootstrapResponse, status_code=status.HTTP_200_OK)
async def bootstrap(
    payload: BootstrapRequest,
    x_bootstrap_secret: str | None = Header(default=None, alias="X-Bootstrap-Secret"),
) -> BootstrapResponse:
    """Called ONCE by LeadPulse CRM immediately after this container starts.

    Subsequent calls overwrite the runtime configuration (useful for credential
    rotation) but require the same ``X-Bootstrap-Secret`` header that is baked
    into the ECS task definition.
    """
    if x_bootstrap_secret is None or x_bootstrap_secret != settings.BOOTSTRAP_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Bootstrap-Secret",
        )

    cfg = RuntimeConfig(
        mongodb_url=payload.mongodb_url,
        mongodb_db=payload.mongodb_db,
        leadpulse_url=str(payload.leadpulse_url).rstrip("/"),
        leadpulse_token=payload.leadpulse_token,
        instance_id=payload.instance_id,
        sender_agents_per_container=payload.sender_agents_per_container,
        mcp_bootstrap_key=payload.mcp_bootstrap_key,
    )

    # If we were previously configured, tear down the old Mongo connection
    # before reconnecting with new credentials.
    if runtime_config.is_configured():
        await close_mongo_connection()

    await runtime_config.set(cfg)
    await connect_to_mongo()  # opens Mongo using the freshly-injected URL.

    return BootstrapResponse(success=True, data=cfg.redacted())


@router.get("/status", response_model=BootstrapResponse)
async def bootstrap_status() -> BootstrapResponse:
    if not runtime_config.is_configured():
        return BootstrapResponse(success=True, data={"configured": False})
    return BootstrapResponse(success=True, data=runtime_config.get().redacted())

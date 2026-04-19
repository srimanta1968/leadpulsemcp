from pydantic import BaseModel, Field, HttpUrl


class BootstrapRequest(BaseModel):
    """Payload sent by LeadPulse CRM when it starts this MCP container."""

    mongodb_url: str = Field(min_length=10, description="Full Mongo connection URI")
    mongodb_db: str = Field(min_length=1, max_length=64)
    leadpulse_url: HttpUrl = Field(description="LeadPulse CRM base URL for callbacks")
    leadpulse_token: str = Field(
        min_length=16, description="HMAC/bearer token used for CRM <-> MCP calls"
    )
    instance_id: str = Field(min_length=1, max_length=256, description="ECS task id / container id")
    sender_agents_per_container: int = Field(default=4, ge=1, le=16)
    mcp_bootstrap_key: str | None = Field(
        default=None,
        description="Shared secret (MCP_BOOTSTRAP_KEY on CRM) used ONLY for the one-time /api/mcp/register HMAC. Required if the CRM enforces it.",
    )


class BootstrapResponse(BaseModel):
    success: bool
    data: dict

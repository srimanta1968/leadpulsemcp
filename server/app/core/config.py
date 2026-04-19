"""Static container configuration.

Runtime credentials (Mongo URL, LeadPulse URL / token, MCP_BOOTSTRAP_KEY)
arrive at runtime via POST /api/v1/bootstrap from the CRM — they do NOT
belong here.
"""
from __future__ import annotations

import json
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_CORS = (
    "http://localhost:3000,"
    "http://localhost:5173,"
    "https://projexlight.com,"
    "https://dev.projexlight.com,"
    "https://leadpulse.projexlight.com"
)


class Settings(BaseSettings):
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # Shared secret used to authenticate the ONE-TIME POST /api/v1/bootstrap
    # from LeadPulse CRM. Provision via ECS Secrets Manager in prod.
    BOOTSTRAP_SECRET: str = "change-me-in-ecs-task-definition"

    # Kept as ``str`` (not List[str]) so pydantic-settings does not try to
    # JSON-decode it from the .env file. Readers should use ``cors_origins``.
    CORS_ORIGINS: str = _DEFAULT_CORS

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> List[str]:
        raw = self.CORS_ORIGINS.strip()
        if not raw:
            return []
        if raw.startswith("["):
            return list(json.loads(raw))
        return [item.strip() for item in raw.split(",") if item.strip()]


settings = Settings()

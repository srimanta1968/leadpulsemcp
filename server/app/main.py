"""Application entrypoint.

Startup order:
  1. Configure structured logging.
  2. Start the FastAPI lifespan. MCP is NOT configured yet — it waits for
     the CRM to call POST /api/v1/bootstrap.
  3. Bootstrap injects mongodb_url + leadpulse_url + leadpulse_token +
     instance_id and kicks off Mongo connect.
  4. A background initializer calls /api/mcp/register (to receive HMAC secret)
     and then starts the supervisor with: extraction, N sender agents,
     hygiene, heartbeat.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents import extraction, heartbeat, hygiene, sender
from app.agents.supervisor import supervisor
from app.api.v1 import router as api_v1_router
from app.api.v1.endpoints import health as health_ep
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.runtime_config import RuntimeConfig, runtime_config
from app.db.mongodb import close_mongo_connection, connect_to_mongo
from app.services import pending_crm_events
from app.services.leadpulse_client import leadpulse_client  # noqa: F401 — imported for singleton side-effects

log = get_logger(__name__)

_stop_event = asyncio.Event() if False else None  # created inside lifespan


def _try_bootstrap_from_env() -> RuntimeConfig | None:
    """If ECS passed all required vars, build a RuntimeConfig without waiting
    for an external bootstrap call. Returns None if config is incomplete.

    Required env:
      LEADPULSE_URL, LEADPULSE_TOKEN, MCP_BOOTSTRAP_KEY,
      MONGODB_URL, MONGODB_DB
    Optional env:
      INSTANCE_ID (else derived from ECS_CONTAINER_METADATA_URI_V4 or hostname+uuid)
      SENDER_AGENTS_PER_CONTAINER (default 4)
    """
    required = ("LEADPULSE_URL", "LEADPULSE_TOKEN", "MCP_BOOTSTRAP_KEY", "MONGODB_URL", "MONGODB_DB")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        if any(os.environ.get(k) for k in required):
            # Partial config — operator error; loud warning.
            log.warning(
                "auto_bootstrap_incomplete_env",
                extra={"extra_payload": {"missing": missing}},
            )
        return None

    # Derive instance_id if the operator didn't pass one.
    instance_id = os.environ.get("INSTANCE_ID") or _derive_instance_id()

    sender_agents = int(os.environ.get("SENDER_AGENTS_PER_CONTAINER", "4"))
    sender_agents = max(1, min(16, sender_agents))

    return RuntimeConfig(
        mongodb_url=os.environ["MONGODB_URL"],
        mongodb_db=os.environ["MONGODB_DB"],
        leadpulse_url=os.environ["LEADPULSE_URL"].rstrip("/"),
        leadpulse_token=os.environ["LEADPULSE_TOKEN"],
        instance_id=instance_id,
        sender_agents_per_container=sender_agents,
        mcp_bootstrap_key=os.environ["MCP_BOOTSTRAP_KEY"],
    )


def _derive_instance_id() -> str:
    """Prefer the ECS task ARN (tail) when available; fall back to hostname+uuid."""
    task_arn = os.environ.get("ECS_TASK_ARN") or os.environ.get("AWS_ECS_TASK_ARN")
    if task_arn:
        return task_arn
    # ECS injects ECS_CONTAINER_METADATA_URI_V4 — the task id is fetchable from
    # it at runtime, but doing so here would block startup. Skip and use host.
    host = os.environ.get("HOSTNAME") or "mcp"
    return f"{host}-{uuid.uuid4().hex[:8]}"


async def _wait_and_start_agents() -> None:
    """Bootstrap (from env if possible, else wait for external call),
    then register with CRM and start agent loops.
    """
    env_cfg = _try_bootstrap_from_env()
    if env_cfg is not None:
        log.info(
            "auto_bootstrap_from_env",
            extra={"extra_payload": env_cfg.redacted()},
        )
        await runtime_config.set(env_cfg)
        await connect_to_mongo()

    cfg = await runtime_config.wait_until_ready()
    log.info("bootstrap_received", extra={"extra_payload": cfg.redacted()})

    await heartbeat.register_once()

    K = cfg.sender_agents_per_container
    supervisor.register("heartbeat", heartbeat.run)
    supervisor.register("extraction", extraction.run)
    supervisor.register("hygiene", hygiene.run)
    for i in range(K):
        agent_uid = f"sender_{i}"
        is_sweeper = i == 0

        def _factory(uid: str = agent_uid, sweeper: bool = is_sweeper):
            async def _run() -> None:
                await sender.run(uid, is_sweeper=sweeper)
            return _run
        supervisor.register(agent_uid, _factory())

    async def _replay_loop() -> None:
        # Drain pending_crm_events whenever CRM is reachable.
        while True:
            try:
                from app.db.mongodb import get_db
                db = get_db()
                async def _post(endpoint: str, payload: dict) -> dict:
                    method = "POST"
                    return await leadpulse_client._request(method, endpoint, json_body=payload)  # type: ignore[attr-defined]
                await pending_crm_events.drain_once(db, _post)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(30)

    supervisor.register("pending_events_replay", _replay_loop)

    await supervisor.start_all()
    log.info("supervisor_started", extra={"extra_payload": {"sender_agents": K}})


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    log.info("mcp_booting")

    stop_event = asyncio.Event()
    initializer = asyncio.create_task(_wait_and_start_agents(), name="mcp-initializer")

    # Install SIGTERM handler as soon as possible (best-effort on Windows).
    try:
        from app.agents import heartbeat as hb
        hb.install_sigterm_handler(stop_event)
    except Exception:  # noqa: BLE001
        log.exception("sigterm_handler_install_failed")

    try:
        yield
    finally:
        stop_event.set()
        initializer.cancel()
        try:
            await supervisor.stop_all(timeout=60.0)
        except Exception:  # noqa: BLE001
            log.exception("supervisor_shutdown_error")
        await close_mongo_connection()
        log.info("mcp_shutdown_complete")


app = FastAPI(
    title="Leadpulse MCP",
    description="Managed Campaign Processor — external campaign email delivery agent",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

# /health at the root — what Docker/ECS liveness probes use.
app.include_router(health_ep.router, tags=["health"])
app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

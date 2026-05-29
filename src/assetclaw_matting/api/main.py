from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.logging_setup import setup_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings.ensure_dirs()
    setup_logging(settings.log_dir, name="gateway")
    db_path = settings.data_dir / "assetclaw.db"
    init_db(db_path)
    create_tables()
    log.info(
        "AssetClaw Win3090 Skill Node — %s:%s  brain=%s  fake=%s  skills=%s",
        settings.gateway_host,
        settings.gateway_port,
        settings.brain_provider,
        settings.comfyui_fake_mode,
        settings.skill_api_enabled,
    )
    yield
    log.info("Gateway shutting down")


app = FastAPI(
    title="AssetClaw Win3090 Skill Node",
    version="0.4.0",
    description=(
        "Feishu Channel + Pluggable Brain Router + Skill Gateway + "
        "MCP Server + ComfyUI Batch Worker\n\n"
        "3090 GPU only runs ComfyUI — no local LLM."
    ),
    lifespan=lifespan,
)

from assetclaw_matting.api.routes_feishu import router as feishu_router      # noqa: E402
from assetclaw_matting.api.routes_worker import router as worker_router      # noqa: E402
from assetclaw_matting.api.routes_admin import router as admin_router        # noqa: E402
from assetclaw_matting.api.routes_skills import router as skills_router      # noqa: E402
from assetclaw_matting.api.routes_arkclaw import router as arkclaw_router    # noqa: E402
from assetclaw_matting.api.routes_brain import router as brain_router        # noqa: E402
from assetclaw_matting.mcp_server.server import router as mcp_router         # noqa: E402

app.include_router(feishu_router)
app.include_router(worker_router)
app.include_router(admin_router)
app.include_router(skills_router)
app.include_router(arkclaw_router)
app.include_router(brain_router)
app.include_router(mcp_router)

try:
    from assetclaw_matting.api.routes_openclaw import router as openclaw_router  # noqa: E402
    app.include_router(openclaw_router)
except ImportError:
    pass


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {
        "ok": True,
        "service": "assetclaw-win3090-skill-node",
        "version": "0.4.0",
        "brain_provider": settings.brain_provider,
        "fake_mode": settings.comfyui_fake_mode,
        "skill_api_enabled": settings.skill_api_enabled,
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
    }


@app.exception_handler(Exception)
async def unhandled(request: object, exc: Exception) -> JSONResponse:
    log.exception("Unhandled exception")
    return JSONResponse(status_code=500, content={"error": str(exc)})

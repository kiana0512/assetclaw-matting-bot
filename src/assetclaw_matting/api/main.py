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
    init_db(settings.data_db_path)
    create_tables()
    log.info("AssetClaw gateway started on %s:%s", settings.gateway_host, settings.gateway_port)
    yield


app = FastAPI(
    title="AssetClaw Win3090 Animation Butler",
    version="1.0.0",
    description="Feishu -> LLM Proxy Brain -> Skills -> Win3090",
    lifespan=lifespan,
)

from assetclaw_matting.api.routes_admin import router as admin_router  # noqa: E402
from assetclaw_matting.api.routes_brain import router as brain_router  # noqa: E402
from assetclaw_matting.api.routes_feishu import router as feishu_router  # noqa: E402
from assetclaw_matting.api.routes_health import router as health_router  # noqa: E402
from assetclaw_matting.api.routes_skills import router as skills_router  # noqa: E402

app.include_router(health_router)
app.include_router(feishu_router)
app.include_router(brain_router)
app.include_router(skills_router)
app.include_router(admin_router)


@app.exception_handler(Exception)
async def unhandled(_: object, exc: Exception) -> JSONResponse:
    log.exception("unhandled gateway exception")
    return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

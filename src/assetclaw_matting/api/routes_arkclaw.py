"""ArkClaw-specific API routes.

- /arkclaw/status    — show connection config (no secrets)
- /arkclaw/webhook   — receive events/callbacks from ArkClaw cloud
- /arkclaw/context   — return current machine context (for debugging)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/arkclaw", tags=["arkclaw"])


def _verify_arkclaw(x_arkclaw_token: str = Header(default="")) -> None:
    from assetclaw_matting.config import settings
    if x_arkclaw_token and x_arkclaw_token != settings.skill_api_token:
        raise HTTPException(status_code=401, detail="Invalid ArkClaw token")


@router.get("/status")
async def arkclaw_status() -> JSONResponse:
    from assetclaw_matting.config import settings
    return JSONResponse({
        "arkclaw_enabled": settings.arkclaw_enabled,
        "arkclaw_base_url": settings.arkclaw_base_url if settings.arkclaw_enabled else "(disabled)",
        "message_mode": settings.arkclaw_message_mode,
        "skill_api_enabled": settings.skill_api_enabled,
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
    })


@router.get("/context")
async def arkclaw_context() -> JSONResponse:
    """Return current machine context (what ArkClaw would receive)."""
    from assetclaw_matting.arkclaw.context_builder import build_machine_context
    ctx = build_machine_context()
    return JSONResponse(ctx)


@router.post("/webhook")
async def arkclaw_webhook(payload: dict) -> JSONResponse:
    """Receive events or callbacks from the ArkClaw cloud agent."""
    log.info("ArkClaw webhook: type=%s", payload.get("type", "unknown"))
    return JSONResponse({"ok": True, "received": True})

"""OpenClaw webhook and status routes.

These allow the cloud OpenClaw agent to push skill call results
back to the gateway, or to check connection status.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/openclaw", tags=["openclaw"])


def _verify_openclaw(x_openclaw_token: str = Header(default="")) -> None:
    from assetclaw_matting.config import settings
    if x_openclaw_token != settings.skill_api_token:
        raise HTTPException(status_code=401, detail="Invalid OpenClaw token")


@router.get("/status")
async def openclaw_status() -> JSONResponse:
    from assetclaw_matting.config import settings
    return JSONResponse({
        "openclaw_enabled": settings.openclaw_enabled,
        "openclaw_base_url": settings.openclaw_base_url if settings.openclaw_enabled else "",
        "message_mode": settings.openclaw_message_mode,
        "skill_api_enabled": settings.skill_api_enabled,
    })


@router.post("/webhook")
async def openclaw_webhook(payload: dict) -> JSONResponse:
    """Receive events/callbacks from the OpenClaw cloud agent.

    Currently logs the payload. Future: handle skill result confirmations,
    multi-step orchestration signals, etc.
    """
    log.info("OpenClaw webhook received: type=%s", payload.get("type", "unknown"))
    return JSONResponse({"ok": True, "received": True})

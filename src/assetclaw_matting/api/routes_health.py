from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    from assetclaw_matting.config import settings

    return {
        "ok": True,
        "service": "assetclaw-win3090-animation-butler",
        "brain_provider": settings.brain_provider,
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
        "comfyui_fake_mode": settings.comfyui_fake_mode,
    }

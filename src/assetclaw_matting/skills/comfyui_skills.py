"""Skill implementations for ComfyUI status checks."""
from __future__ import annotations

from typing import Any


def comfyui_status() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    if settings.comfyui_fake_mode:
        return {
            "status": "fake_online",
            "fake_mode": True,
            "url": settings.comfyui_url,
            "message": "ComfyUI is mocked (COMFYUI_FAKE_MODE=true)",
        }
    from assetclaw_matting.comfyui.client import comfyui_client
    try:
        info = comfyui_client.check_health()
        return {
            "status": "online",
            "fake_mode": False,
            "url": settings.comfyui_url,
            "system_stats": info,
        }
    except Exception as exc:
        return {
            "status": "offline",
            "fake_mode": False,
            "url": settings.comfyui_url,
            "error": str(exc),
        }

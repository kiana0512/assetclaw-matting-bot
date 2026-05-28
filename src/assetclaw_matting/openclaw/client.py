"""OpenClaw Cloud Agent HTTP client.

When OPENCLAW_ENABLED=false, all methods return mocked responses.
When OPENCLAW_ENABLED=true, calls the configured OpenClaw HTTP API.

The local 3090 GPU is NEVER used by this module.
All AI inference happens on the cloud side.
"""
from __future__ import annotations

import logging
from typing import Any

from assetclaw_matting.openclaw.schemas import OpenClawRequest, OpenClawResponse

log = logging.getLogger(__name__)

# API path constants — update these if the OpenClaw API changes
_PATH_CHAT = "/api/v1/chat"
_PATH_EVENT = "/api/v1/events"


class OpenClawClient:
    """HTTP client for the OpenClaw cloud agent API."""

    def send_message(
        self,
        conversation_id: str,
        user_id: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> OpenClawResponse:
        """Send a user message to OpenClaw and return the agent's response."""
        from assetclaw_matting.config import settings

        if not settings.openclaw_enabled:
            return _mock_response()

        payload = OpenClawRequest(
            conversation_id=conversation_id,
            user_id=user_id,
            text=text,
            machine_context=context or _build_machine_context(),
            available_skills=_get_skill_names(),
        )
        return self._post(_PATH_CHAT, payload.model_dump())

    def send_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a system event to OpenClaw (e.g., batch completed)."""
        from assetclaw_matting.config import settings
        if not settings.openclaw_enabled:
            log.debug("OpenClaw disabled — event %s dropped", event_type)
            return {"ok": True, "mocked": True}
        return self._post(_PATH_EVENT, {"type": event_type, "payload": payload})

    def _post(self, path: str, body: dict[str, Any]) -> OpenClawResponse:
        import requests
        from assetclaw_matting.config import settings

        url = f"{settings.openclaw_base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {settings.openclaw_api_key}",
            "Content-Type": "application/json",
            "X-Bot-Id": settings.openclaw_bot_id,
        }
        try:
            resp = requests.post(
                url, json=body, headers=headers,
                timeout=settings.openclaw_timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            return OpenClawResponse.model_validate(data)
        except Exception as exc:
            log.error("OpenClaw API call failed: %s", exc)
            return OpenClawResponse(
                type="text",
                text=f"OpenClaw 服务暂时不可用：{exc}",
            )


def _mock_response() -> OpenClawResponse:
    return OpenClawResponse(
        type="text",
        text=(
            "当前 OpenClaw Agent 未启用（OPENCLAW_ENABLED=false）。\n"
            "请使用本地命令模式：help / queue / batch list / batch status <id>"
        ),
    )


def _build_machine_context() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    return {
        "machine_id": settings.worker_id,
        "comfyui_fake_mode": settings.comfyui_fake_mode,
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
    }


def _get_skill_names() -> list[str]:
    try:
        from assetclaw_matting.skills.registry import SKILL_CATALOG
        return [s["name"] for s in SKILL_CATALOG]
    except Exception:
        return []


openclaw_client = OpenClawClient()

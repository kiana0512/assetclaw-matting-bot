"""ArkClaw Cloud API HTTP client.

When ARKCLAW_ENABLED=false: all methods return informative mock responses.
When ARKCLAW_ENABLED=true:  calls ARKCLAW_BASE_URL with ARKCLAW_API_KEY.

IMPORTANT: This client NEVER uses the local GPU.
All AI inference happens on the ArkClaw cloud.
"""
from __future__ import annotations

import logging
from typing import Any

from assetclaw_matting.arkclaw.schemas import ArkClawRequest, ArkClawResponse
from assetclaw_matting.arkclaw import protocol as _proto

log = logging.getLogger(__name__)


class ArkClawClient:
    """HTTP adapter for the ArkClaw Enterprise Brain API."""

    def send_message(
        self,
        conversation_id: str,
        user_id: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> ArkClawResponse:
        """Send a user message and receive the agent's response."""
        from assetclaw_matting.config import settings

        if not settings.arkclaw_enabled:
            return _disabled_response()

        from assetclaw_matting.arkclaw.context_builder import (
            build_machine_context,
            build_queue_summary,
        )

        payload = ArkClawRequest(
            conversation_id=conversation_id,
            user_id=user_id,
            text=text,
            machine_context=context or build_machine_context(),
            available_skills=_get_skill_names(),
            recent_queue_summary=build_queue_summary(),
            security_policy_summary=_proto.SECURITY_POLICY_SUMMARY,
        )
        return self._post(_proto.PATH_CHAT, payload.model_dump())

    def send_skill_result(
        self,
        conversation_id: str,
        request_id: str,
        skill_result: dict[str, Any],
    ) -> ArkClawResponse:
        """Send a skill execution result back to ArkClaw for summarisation."""
        from assetclaw_matting.config import settings
        if not settings.arkclaw_enabled:
            return _disabled_response()
        return self._post(
            _proto.PATH_SKILL_RESULT,
            {
                "conversation_id": conversation_id,
                "request_id": request_id,
                "skill_result": skill_result,
            },
        )

    def send_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Notify ArkClaw of a system event (batch completed, worker error, etc.)."""
        from assetclaw_matting.config import settings
        if not settings.arkclaw_enabled:
            log.debug("ArkClaw disabled — event %s dropped", event_type)
            return {"ok": True, "mocked": True}
        try:
            resp = self._post(
                _proto.PATH_EVENT,
                {"type": event_type, "payload": payload},
            )
            return {"ok": True, "type": resp.type}
        except Exception as exc:
            log.warning("ArkClaw event send failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _post(self, path: str, body: dict[str, Any]) -> ArkClawResponse:
        import requests
        from assetclaw_matting.config import settings

        url = f"{settings.arkclaw_base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {settings.arkclaw_api_key}",
            "Content-Type": "application/json",
            "X-Bot-Id": settings.arkclaw_bot_id,
            "X-Workspace-Id": settings.arkclaw_workspace_id,
            "X-Node-Id": settings.worker_id,
        }
        try:
            resp = requests.post(
                url, json=body, headers=headers,
                timeout=settings.arkclaw_timeout_seconds,
            )
            resp.raise_for_status()
            return ArkClawResponse.model_validate(resp.json())
        except Exception as exc:
            log.error("ArkClaw API call to %s failed: %s", path, exc)
            return ArkClawResponse(
                type="text",
                text=f"ArkClaw 服务暂时不可用，请稍后再试。（{exc}）",
            )


def _disabled_response() -> ArkClawResponse:
    return ArkClawResponse(
        type="text",
        text=(
            "ArkClaw 企业版未启用（ARKCLAW_ENABLED=false）。\n"
            "当前只能使用本地命令：help / queue / batch list / batch status <id>\n"
            "或直接调用 Skill API（/skills/v1/call）进行测试。"
        ),
    )


def _get_skill_names() -> list[str]:
    try:
        from assetclaw_matting.skills.registry import SKILL_CATALOG
        return [s["name"] for s in SKILL_CATALOG if s["implemented"]]
    except Exception:
        return []


arkclaw_client = ArkClawClient()

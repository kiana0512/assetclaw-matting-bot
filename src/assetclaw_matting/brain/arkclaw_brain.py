"""ArkClaw Enterprise Brain adapter."""
from __future__ import annotations

import logging

from assetclaw_matting.brain.base import BrainProvider
from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse, BrainToolCall

log = logging.getLogger(__name__)


class ArkClawBrain(BrainProvider):
    name = "arkclaw"

    def is_available(self) -> bool:
        from assetclaw_matting.config import settings
        return settings.arkclaw_enabled and bool(settings.arkclaw_base_url)

    def handle_message(
        self, message: BrainMessage, context: BrainContext
    ) -> BrainResponse:
        from assetclaw_matting.config import settings
        if not settings.arkclaw_enabled:
            return BrainResponse(
                text="ArkClaw 企业版未启用（ARKCLAW_ENABLED=false）。",
                provider=self.name,
            )

        from assetclaw_matting.arkclaw.client import arkclaw_client
        try:
            arkclaw_resp = arkclaw_client.send_message(
                conversation_id=message.conversation_id,
                user_id=message.user_id,
                text=message.text,
            )
        except Exception as exc:
            log.error("ArkClaw call failed: %s", exc)
            return BrainResponse(
                text=f"ArkClaw 连接失败：{exc}",
                provider=self.name,
            )

        tool_calls = [
            BrainToolCall(
                skill=tc.skill,
                arguments=tc.arguments,
                call_id=tc.call_id,
            )
            for tc in arkclaw_resp.tool_calls
        ]

        skill_results = self._execute_tool_calls(tool_calls) if tool_calls else []

        reply = arkclaw_resp.text
        if skill_results:
            reply += "\n" + self._format_skill_results(skill_results)

        response = BrainResponse(
            text=reply or "完成。",
            tool_calls=tool_calls,
            provider=self.name,
        )
        self._log_message(message, response, skill_results)
        return response

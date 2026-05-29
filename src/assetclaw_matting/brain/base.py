"""Abstract base class for all Brain Providers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from assetclaw_matting.brain.schemas import BrainContext, BrainMessage, BrainResponse, BrainToolCall

log = logging.getLogger(__name__)


class BrainProvider(ABC):
    """A pluggable brain that handles messages and can call skills."""

    name: str = "base"

    @abstractmethod
    def handle_message(
        self,
        message: BrainMessage,
        context: BrainContext,
    ) -> BrainResponse:
        """Process a user message and return a response.

        May internally call skills via _execute_tool_calls().
        Must never execute shell commands or access arbitrary paths.
        """

    def is_available(self) -> bool:
        """Return True if this provider is configured and ready."""
        return True

    def _execute_tool_calls(
        self, tool_calls: list[BrainToolCall]
    ) -> list[dict[str, Any]]:
        """Execute a list of tool calls via the Skill Registry."""
        from assetclaw_matting.skills.registry import call_skill
        results: list[dict[str, Any]] = []
        for tc in tool_calls:
            try:
                result = call_skill(
                    tc.skill,
                    tc.arguments,
                    requested_by=self.name,
                    request_id=tc.call_id or "",
                )
                results.append(result)
                log.debug("Brain %s executed skill %s: ok=%s", self.name, tc.skill, result.get("ok"))
            except Exception as exc:
                log.error("Skill %s failed: %s", tc.skill, exc)
                results.append({"ok": False, "skill": tc.skill, "error": str(exc)})
        return results

    def _log_message(
        self,
        message: BrainMessage,
        response: BrainResponse,
        tool_call_results: list[dict[str, Any]] | None = None,
    ) -> None:
        """Write to brain_messages audit table."""
        import json
        from assetclaw_matting.db.brain_message_repo import insert_brain_message
        try:
            insert_brain_message(
                provider=self.name,
                channel=message.channel,
                conversation_id=message.conversation_id,
                user_id=message.user_id,
                message_text=message.text,
                response_text=response.text,
                tool_calls_json=json.dumps(
                    [tc.model_dump() for tc in response.tool_calls], default=str
                ),
                raw_json=json.dumps(response.raw, default=str),
            )
        except Exception:
            log.debug("Failed to log brain message", exc_info=True)

    def _format_skill_results(self, results: list[dict[str, Any]]) -> str:
        """Format skill execution results into a human-readable string."""
        parts: list[str] = []
        for r in results:
            skill = r.get("skill", "?")
            if r.get("ok"):
                result_data = r.get("result", {})
                # Try to extract meaningful summary
                if "batch_id" in result_data:
                    parts.append(
                        f"批次 {result_data['batch_id']} 已创建，共 {result_data.get('total_count', '?')} 张图"
                    )
                elif "queued_tasks" in result_data:
                    parts.append(
                        f"队列：排队 {result_data['queued_tasks']} / "
                        f"运行中 {result_data['running_tasks']} / "
                        f"失败 {result_data['failed_tasks']}"
                    )
                elif "status" in result_data:
                    parts.append(f"[{skill}] 状态: {result_data['status']}")
                else:
                    parts.append(f"[{skill}] 完成")
            else:
                parts.append(f"[{skill}] 失败: {r.get('error', '未知错误')}")
        return "\n".join(parts)

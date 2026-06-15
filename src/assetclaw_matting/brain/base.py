from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
from typing import Any

from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse, ToolCall

log = logging.getLogger(__name__)


class BrainProvider(ABC):
    name = "base"

    def is_available(self) -> bool:
        return True

    @abstractmethod
    def handle_message(self, message: BrainMessage) -> BrainResponse:
        raise NotImplementedError

    def execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        conversation_id: str = "",
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        from assetclaw_matting.config import settings
        from assetclaw_matting.db.repos import create_pending_confirmation
        from assetclaw_matting.ops_trace import trace
        from assetclaw_matting.progress import notify_progress
        from assetclaw_matting.skills.registry import call_skill
        from assetclaw_matting.skills.registry import get_skill_meta

        results: list[dict[str, Any]] = []
        for tool_call in tool_calls[: settings.brain_max_tool_calls]:
            args = dict(tool_call.arguments or {})
            if tool_call.skill.startswith("memory.") and conversation_id:
                if not args.get("scope") or args.get("scope") == "global":
                    args["scope"] = conversation_id
            trace(
                "skill.call",
                conversation_id=conversation_id,
                provider=self.name,
                skill=tool_call.skill,
                arguments=args,
            )
            meta = get_skill_meta(tool_call.skill) or {}
            if meta.get("requires_confirmation"):
                confirmation_id = create_pending_confirmation(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    skill=tool_call.skill,
                    arguments=args,
                )
                message = f"需要确认：{tool_call.skill}\n回复：确认执行 {confirmation_id}"
                if tool_call.skill == "comfyui.run_start":
                    try:
                        from assetclaw_matting.skills.comfyui_skills import preview_run_start_confirmation

                        message = preview_run_start_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "cherry.run_start":
                    try:
                        from assetclaw_matting.skills.cherry_skills import preview_run_start_confirmation

                        message = preview_run_start_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "frame.run_start":
                    try:
                        from assetclaw_matting.skills.frame_skills import preview_run_start_confirmation

                        message = preview_run_start_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "pipeline.run_start":
                    try:
                        from assetclaw_matting.skills.pipeline_skills import preview_run_start_confirmation

                        message = preview_run_start_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "animation_flow.start":
                    try:
                        from assetclaw_matting.skills.animation_flow_skills import preview_run_start_confirmation

                        message = preview_run_start_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "animation_flow.resume":
                    try:
                        from assetclaw_matting.skills.animation_flow_skills import preview_run_resume_confirmation

                        message = preview_run_resume_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "animation.manual_smooth_current":
                    try:
                        from assetclaw_matting.skills.animation_ops_skills import preview_manual_smooth_current_confirmation

                        message = preview_manual_smooth_current_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "animation.rerun_from_videos":
                    try:
                        from assetclaw_matting.skills.animation_ops_skills import preview_rerun_from_videos_confirmation

                        message = preview_rerun_from_videos_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "unity_tools.atlas_report":
                    try:
                        from assetclaw_matting.skills.unity_tools_skills import preview_atlas_report_confirmation

                        message = preview_atlas_report_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "unity_tools.rename_run":
                    try:
                        from assetclaw_matting.skills.unity_tools_skills import preview_rename_confirmation

                        message = preview_rename_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                elif tool_call.skill == "p4.cleanup_cl":
                    try:
                        from assetclaw_matting.skills.p4_skills import preview_cleanup_cl_confirmation

                        message = preview_cleanup_cl_confirmation(args, confirmation_id)
                    except Exception:
                        pass
                result = {
                    "ok": False,
                    "skill": tool_call.skill,
                    "needs_confirmation": True,
                    "confirmation_id": confirmation_id,
                    "message": message,
                }
                trace(
                    "skill.confirmation_required",
                    conversation_id=conversation_id,
                    provider=self.name,
                    skill=tool_call.skill,
                    confirmation_id=confirmation_id,
                    arguments=args,
                )
                notify_progress(f"需要确认：{tool_call.skill}，回复“确认执行 {confirmation_id}”后才会继续。")
                results.append(result)
                continue
            notify_progress(f"正在调用工具：{tool_call.skill}")
            context_token = None
            try:
                from assetclaw_matting.runtime_context import get_runtime_context, reset_runtime_context, set_runtime_context

                current_context = get_runtime_context()
                merged_context = {
                    **current_context,
                    "conversation_id": conversation_id or current_context.get("conversation_id", ""),
                    "user_id": user_id or current_context.get("user_id", ""),
                    "requested_by": self.name,
                }
                context_token = set_runtime_context(**merged_context)
                result = call_skill(tool_call.skill, args, requested_by=self.name)
            finally:
                if context_token is not None:
                    reset_runtime_context(context_token)
            trace(
                "skill.result",
                conversation_id=conversation_id,
                provider=self.name,
                skill=tool_call.skill,
                ok=result.get("ok"),
                result=result,
            )
            results.append(result)
        return results

    def log_message(
        self,
        message: BrainMessage,
        response: BrainResponse,
        raw: dict[str, Any] | None = None,
    ) -> None:
        from assetclaw_matting.db.repos import insert_brain_message

        try:
            insert_brain_message(
                provider=self.name,
                channel=message.channel,
                conversation_id=message.conversation_id,
                user_id=message.user_id,
                message_text=message.text,
                response_text=response.text,
                tool_calls_json=json.dumps([tc.model_dump() for tc in response.tool_calls], ensure_ascii=False),
                raw_json=json.dumps(raw or response.raw, ensure_ascii=False, default=str),
            )
        except Exception:
            log.debug("failed to audit brain message", exc_info=True)

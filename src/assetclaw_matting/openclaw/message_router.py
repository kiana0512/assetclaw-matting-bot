"""Message routing logic: decides whether a Feishu message goes to local
commands, OpenClaw, or both, based on OPENCLAW_MESSAGE_MODE."""
from __future__ import annotations

import logging

from assetclaw_matting.feishu.command_runner import execute_command, is_known_command
from assetclaw_matting.openclaw.schemas import OpenClawResponse

log = logging.getLogger(__name__)

_OPENCLAW_DISABLED_MSG = (
    "当前 OpenClaw Agent 未启用。\n"
    "请输入 help 查看本地可用命令。"
)


def route(
    text: str,
    chat_id: str,
    sender_id: str,
) -> str:
    """Route a Feishu text message and return the reply string.

    Mode logic:
    - local_command_first: run hardcoded commands first; fallback to OpenClaw.
    - relay_only: always send to OpenClaw.
    """
    from assetclaw_matting.config import settings

    mode = settings.openclaw_message_mode

    if mode == "local_command_first":
        if is_known_command(text):
            return execute_command(text, chat_id)
        # Not a known command — fall through to OpenClaw
        return _forward_to_openclaw(text, chat_id, sender_id)

    if mode == "relay_only":
        return _forward_to_openclaw(text, chat_id, sender_id)

    # Unknown mode — safe default
    log.warning("Unknown OPENCLAW_MESSAGE_MODE=%s, defaulting to local", mode)
    return execute_command(text, chat_id) or _OPENCLAW_DISABLED_MSG


def _forward_to_openclaw(text: str, chat_id: str, sender_id: str) -> str:
    from assetclaw_matting.config import settings
    from assetclaw_matting.openclaw.client import openclaw_client

    if not settings.openclaw_enabled:
        return _OPENCLAW_DISABLED_MSG

    try:
        response = openclaw_client.send_message(
            conversation_id=chat_id,
            user_id=sender_id,
            text=text,
        )
        return _process_response(response, chat_id)
    except Exception as exc:
        log.exception("OpenClaw forwarding failed")
        return f"OpenClaw 处理失败：{exc}"


def _process_response(response: OpenClawResponse, chat_id: str) -> str:
    """Process OpenClaw response: handle tool_calls, return final text."""
    if response.type == "text":
        return response.text

    if response.type in ("tool_call", "mixed"):
        results: list[str] = []
        if response.text:
            results.append(response.text)

        for tc in response.tool_calls:
            skill_result = _execute_skill(tc.skill, tc.arguments)
            results.append(f"[{tc.skill}] {skill_result.get('message', str(skill_result))}")

        return "\n".join(results) if results else "完成。"

    return response.text or "（空响应）"


def _execute_skill(skill_name: str, arguments: dict) -> dict:
    from assetclaw_matting.skills.registry import call_skill
    try:
        return call_skill(
            skill_name, arguments,
            requested_by="openclaw",
            request_id="",
        )
    except Exception as exc:
        log.error("Skill execution failed: %s %s", skill_name, exc)
        return {"ok": False, "error": str(exc)}

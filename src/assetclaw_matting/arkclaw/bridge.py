"""ArkClaw Bridge — entry point for Feishu text message routing.

Flow:
  Feishu text
    → try local hardcoded commands (if local_command_first)
    → if unknown AND arkclaw_enabled: send to ArkClaw cloud
    → if ArkClaw returns text: reply to Feishu
    → if ArkClaw returns tool_calls: execute via Skill Registry → reply
    → log everything to arkclaw_messages table
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from assetclaw_matting.arkclaw.schemas import ArkClawResponse
from assetclaw_matting.feishu.client import feishu_client
from assetclaw_matting.feishu.command_runner import execute_command, is_known_command

log = logging.getLogger(__name__)

_ARKCLAW_DISABLED_MSG = (
    "ArkClaw 企业版未启用。\n"
    "可用本地命令：help / queue / batch list / batch status <id> / task status <id>\n"
    "也可直接调用 Skill API 测试：POST /skills/v1/call"
)


def handle_feishu_text_message(
    chat_id: str,
    sender_id: str,
    message_id: str,
    text: str,
) -> None:
    """Route a Feishu text message and send reply."""
    try:
        reply = _route(text=text, chat_id=chat_id, sender_id=sender_id)
    except Exception as exc:
        log.exception("Message routing failed for message_id=%s", message_id)
        reply = f"处理失败，请稍后重试。（{exc}）"

    feishu_client.reply_text(message_id, reply)
    _record(chat_id, sender_id, text, reply)


def _route(text: str, chat_id: str, sender_id: str) -> str:
    from assetclaw_matting.config import settings

    mode = settings.arkclaw_message_mode

    if mode == "local_command_first":
        if is_known_command(text):
            return execute_command(text, chat_id)
        return _forward_to_arkclaw(text, chat_id, sender_id)

    if mode == "relay_only":
        return _forward_to_arkclaw(text, chat_id, sender_id)

    log.warning("Unknown ARKCLAW_MESSAGE_MODE=%s, using local commands", mode)
    result = execute_command(text, chat_id)
    return result or _ARKCLAW_DISABLED_MSG


def _forward_to_arkclaw(text: str, chat_id: str, sender_id: str) -> str:
    from assetclaw_matting.config import settings
    from assetclaw_matting.arkclaw.client import arkclaw_client

    if not settings.arkclaw_enabled:
        return _ARKCLAW_DISABLED_MSG

    try:
        response = arkclaw_client.send_message(
            conversation_id=chat_id,
            user_id=sender_id,
            text=text,
        )
        return _process_response(response, chat_id, sender_id)
    except Exception as exc:
        log.exception("ArkClaw forwarding failed")
        return f"ArkClaw 处理失败：{exc}"


def _process_response(
    response: ArkClawResponse,
    chat_id: str,
    sender_id: str,
) -> str:
    if response.type == "text":
        return response.text

    if response.type in ("tool_call", "mixed"):
        parts: list[str] = []
        if response.text:
            parts.append(response.text)

        for tc in response.tool_calls:
            skill_result = _execute_skill(tc.skill, tc.arguments, tc.call_id, chat_id)
            summary = skill_result.get("message") or str(skill_result.get("result", ""))
            parts.append(f"[{tc.skill}] {summary}")

        return "\n".join(parts) if parts else "任务执行完成。"

    return response.text or "（空响应）"


def _execute_skill(
    skill_name: str,
    arguments: dict[str, Any],
    call_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    from assetclaw_matting.skills.registry import call_skill
    try:
        result = call_skill(
            skill_name, arguments,
            requested_by="arkclaw",
            request_id=call_id,
        )
        # Optionally send result back to ArkClaw
        _try_send_skill_result(conversation_id, call_id, result)
        return result
    except Exception as exc:
        log.error("Skill execution failed: %s %s", skill_name, exc)
        return {"ok": False, "error": str(exc)}


def _try_send_skill_result(
    conversation_id: str,
    call_id: str,
    result: dict[str, Any],
) -> None:
    from assetclaw_matting.config import settings
    if not settings.arkclaw_enabled or not call_id:
        return
    from assetclaw_matting.arkclaw.client import arkclaw_client
    try:
        arkclaw_client.send_skill_result(conversation_id, call_id, result)
    except Exception:
        log.debug("Failed to send skill result to ArkClaw", exc_info=True)


def _record(
    chat_id: str,
    sender_id: str,
    message_text: str,
    arkclaw_reply: str,
) -> None:
    try:
        from assetclaw_matting.db.sqlite import get_connection
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO arkclaw_messages "
                "(conversation_id, user_id, message_text, arkclaw_reply, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, sender_id, message_text, arkclaw_reply, now),
            )
    except Exception:
        log.debug("Failed to record arkclaw message", exc_info=True)

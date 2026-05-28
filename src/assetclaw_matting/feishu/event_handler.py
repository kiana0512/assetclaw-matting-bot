from __future__ import annotations

import json
import logging
from typing import Any

from assetclaw_matting.config import settings
from assetclaw_matting.db import task_repo
from assetclaw_matting.feishu.client import feishu_client
from assetclaw_matting.models.feishu_models import FeishuEventEnvelope

log = logging.getLogger(__name__)

# Re-export for backward compatibility (agent/harness.py imports from here)
from assetclaw_matting.feishu.command_runner import execute_command  # noqa: F401


def handle_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Route an incoming Feishu event. Returns the HTTP response body."""
    if raw.get("type") == "url_verification":
        return _handle_url_verification(raw)

    envelope = FeishuEventEnvelope.model_validate(raw)
    header = envelope.header
    event = envelope.event

    if header is None or event is None:
        return {"ok": True}

    event_id = header.event_id
    event_type = header.event_type

    if event_id and task_repo.event_id_seen(event_id):
        log.debug("Duplicate event_id=%s, skipping", event_id)
        return {"ok": True}

    msg = event.message
    sender = event.sender
    message_id = msg.message_id if msg else ""
    chat_id = msg.chat_id if msg else ""
    sender_id = ""
    if sender and sender.sender_id:
        sender_id = (
            sender.sender_id.get("open_id")
            or sender.sender_id.get("user_id")
            or ""
        )

    if event_id:
        task_repo.insert_event(event_id, event_type, message_id, raw)

    if event_type != "im.message.receive_v1" or msg is None:
        return {"ok": True}

    if msg.message_type == "text":
        _handle_text(message_id, chat_id, sender_id, msg.content)
    else:
        feishu_client.reply_text(
            message_id,
            "AssetClaw 暂不支持该消息类型。\n发送 help 查看可用命令。",
        )

    return {"ok": True}


def _handle_url_verification(raw: dict[str, Any]) -> dict[str, Any]:
    token = raw.get("token", "")
    if token != settings.feishu_verification_token:
        log.warning("URL verification token mismatch")
        return {"error": "invalid token"}
    return {"challenge": raw.get("challenge", "")}


def _handle_text(
    message_id: str, chat_id: str, sender_id: str, content_raw: str
) -> None:
    try:
        content = json.loads(content_raw)
        text = content.get("text", "").strip()
    except Exception:
        text = content_raw.strip()

    # Route through OpenClaw bridge (handles local commands + cloud agent fallback)
    from assetclaw_matting.openclaw.bridge import handle_feishu_text_message
    handle_feishu_text_message(
        chat_id=chat_id,
        sender_id=sender_id,
        message_id=message_id,
        text=text,
    )

from __future__ import annotations

import json
import logging
from typing import Any

from assetclaw_matting.config import settings
from assetclaw_matting.db import task_repo
from assetclaw_matting.feishu.client import feishu_client
from assetclaw_matting.models.feishu_models import FeishuEventEnvelope

log = logging.getLogger(__name__)

# Re-export for backward compat (agent/harness.py imports from here)
from assetclaw_matting.feishu.command_runner import execute_command  # noqa: F401

_IMAGE_REPLY = (
    "当前主流程是目录批量抠图。\n"
    "请把图片放入输入目录，再创建批次：\n"
    "  CLI:   python -m assetclaw_matting.cli.main batch-create ...\n"
    "  API:   POST /admin/batches/create\n"
    "  Skill: POST /skills/v1/call {\"skill\":\"batch.create\",...}\n"
    "单图上传将在后续版本支持。"
)


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
    elif msg.message_type == "image":
        feishu_client.reply_text(message_id, _IMAGE_REPLY)
    else:
        feishu_client.reply_text(
            message_id,
            "暂不支持该消息类型。发送 help 查看可用命令。",
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
        text = json.loads(content_raw).get("text", "").strip()
    except Exception:
        text = content_raw.strip()

    # Route through Brain Router (pluggable: local_command | llm_proxy | arkclaw | claude | ...)
    from assetclaw_matting.brain.schemas import BrainMessage
    from assetclaw_matting.brain import router as brain_router

    msg = BrainMessage(
        channel="feishu",
        conversation_id=chat_id,
        user_id=sender_id,
        text=text,
    )
    try:
        response = brain_router.handle_message(msg)
        reply = response.text or "完成。"
    except Exception as exc:
        log.exception("Brain router failed for message_id=%s", message_id)
        reply = f"处理失败，请稍后重试。（{exc}）"

    feishu_client.reply_text(message_id, reply)

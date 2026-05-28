"""OpenClaw Bridge: entry point for routing Feishu messages.

Receives a Feishu text message, routes it via message_router,
and sends the reply back to Feishu.

Also records the exchange in the agent_messages table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from assetclaw_matting.feishu.client import feishu_client
from assetclaw_matting.openclaw.message_router import route

log = logging.getLogger(__name__)


def handle_feishu_text_message(
    chat_id: str,
    sender_id: str,
    message_id: str,
    text: str,
) -> None:
    """Route a Feishu text message and reply to the user."""
    try:
        reply = route(text=text, chat_id=chat_id, sender_id=sender_id)
    except Exception as exc:
        log.exception("Message routing failed for message_id=%s", message_id)
        reply = f"处理失败，请稍后重试。（{exc}）"

    feishu_client.reply_text(message_id, reply)
    _record_exchange(chat_id, sender_id, text, reply)


def _record_exchange(
    chat_id: str,
    sender_id: str,
    message_text: str,
    agent_reply: str,
) -> None:
    try:
        from assetclaw_matting.db.sqlite import get_connection
        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO agent_messages "
                "(channel, chat_id, sender_id, message_text, agent_reply, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("feishu", chat_id, sender_id, message_text, agent_reply, now),
            )
    except Exception:
        log.warning("Failed to record agent message exchange", exc_info=True)

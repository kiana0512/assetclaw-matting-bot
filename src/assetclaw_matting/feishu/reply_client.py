from __future__ import annotations

import logging

from assetclaw_matting.errors import ErrorEnvelope
from assetclaw_matting.feishu.client import feishu_client
from assetclaw_matting.feishu.models import FeishuMessageEvent
from assetclaw_matting.skills.security import redact_secrets

log = logging.getLogger(__name__)


def send_reply(message_id: str, chat_id: str, text: str) -> bool:
    """Send reply via message_id, fallback to chat_id. Returns True on success."""
    if message_id:
        try:
            feishu_client.reply_text(message_id, text)
            return True
        except Exception as exc:
            log.warning("reply via message_id failed: %s", redact_secrets(str(exc)))
    if chat_id:
        try:
            feishu_client.send_text_to_chat(chat_id, text)
            return True
        except Exception as exc:
            log.warning("reply via chat_id failed: %s", redact_secrets(str(exc)))
    return False


def send_error(event: FeishuMessageEvent, envelope: ErrorEnvelope) -> None:
    """Push a structured error message to feishu."""
    from assetclaw_matting.config import settings

    if not settings.bot_error_push_enabled:
        return
    text = envelope.to_feishu_text()
    ok = send_reply(event.message_id, event.chat_id, text)
    if not ok:
        log.error("failed to push error to feishu trace_id=%s", envelope.trace_id)

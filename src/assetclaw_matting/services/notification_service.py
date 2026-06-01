from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def send_text(chat_id: str, text: str) -> None:
    if not chat_id:
        return
    try:
        from assetclaw_matting.feishu.client import feishu_client

        feishu_client.send_text_to_chat(chat_id, text)
    except Exception:
        log.exception("failed to send notification")

"""
Feishu webhook event handler — LEGACY MODE.

Primary mode is now WebSocket long connection (ws_receiver.py).
This module is only active when FEISHU_EVENT_MODE=webhook.

When FEISHU_EVENT_MODE=ws (default), /feishu/events returns a disabled message.
"""
from __future__ import annotations

import logging
from typing import Any

from assetclaw_matting.config import settings

log = logging.getLogger(__name__)


def handle_event(raw: dict[str, Any]) -> dict[str, Any]:
    # URL verification challenge — always handle regardless of mode
    if raw.get("type") == "url_verification":
        if settings.feishu_event_mode == "webhook":
            return _url_verification(raw)
        # WS mode: URL verification is not needed
        return {"ok": True, "message": "WS mode active. URL verification not required."}

    # In WS mode: reject all incoming webhook events
    if settings.feishu_event_mode != "webhook":
        return {
            "ok": False,
            "message": (
                f"Webhook mode disabled. Current FEISHU_EVENT_MODE={settings.feishu_event_mode}. "
                "Use Feishu long connection receiver (ws_receiver.py) instead."
            ),
        }

    # --- Webhook legacy mode ------------------------------------------------
    header = raw.get("header") or {}
    event = raw.get("event") or {}
    if header.get("event_type") != "im.message.receive_v1":
        return {"ok": True}

    from assetclaw_matting.feishu.message_adapter import from_webhook_dict
    from assetclaw_matting.feishu.processor import process_feishu_message

    feishu_event = from_webhook_dict(raw)
    result = process_feishu_message(feishu_event)
    return {"ok": result.ok}


def _url_verification(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("token") != settings.feishu_verification_token:
        return {"error": "invalid token"}
    return {"challenge": raw.get("challenge", "")}

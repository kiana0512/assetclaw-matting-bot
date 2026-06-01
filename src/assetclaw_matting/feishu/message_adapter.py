from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Optional

from assetclaw_matting.feishu.models import FeishuMessageEvent

log = logging.getLogger(__name__)


def _safe_get(obj: Any, *attrs: str, default: Any = None) -> Any:
    """Safely access nested attributes or dict keys."""
    current = obj
    for attr in attrs:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(attr)
        else:
            current = getattr(current, attr, None)
    return current if current is not None else default


def from_lark_event(data: Any) -> FeishuMessageEvent:
    """Convert a lark_oapi P2ImMessageReceiveV1 SDK object to FeishuMessageEvent."""
    trace_id = uuid.uuid4().hex[:12]

    header = _safe_get(data, "header")
    event = _safe_get(data, "event")

    event_id: Optional[str] = _safe_get(header, "event_id")

    message = _safe_get(event, "message")
    sender = _safe_get(event, "sender")
    sender_id_obj = _safe_get(sender, "sender_id")

    message_id: str = _safe_get(message, "message_id", default="") or ""
    chat_id: str = _safe_get(message, "chat_id", default="") or ""
    chat_type: str = _safe_get(message, "chat_type", default="p2p") or "p2p"
    message_type: str = _safe_get(message, "message_type", default="") or ""
    content_raw: str = _safe_get(message, "content", default="") or ""
    mentions = _safe_get(message, "mentions", default=[]) or []

    open_id: Optional[str] = _safe_get(sender_id_obj, "open_id")
    user_id: Optional[str] = _safe_get(sender_id_obj, "user_id")

    text = _extract_text(message_type, content_raw, mentions)

    return FeishuMessageEvent(
        trace_id=trace_id,
        event_id=event_id,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        open_id=open_id,
        user_id=user_id,
        text=text,
    )


def from_webhook_dict(raw: dict[str, Any]) -> FeishuMessageEvent:
    """Convert a webhook raw dict to FeishuMessageEvent (legacy webhook mode)."""
    trace_id = uuid.uuid4().hex[:12]

    header = raw.get("header") or {}
    event = raw.get("event") or {}

    event_id: Optional[str] = header.get("event_id")

    message = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id_dict = sender.get("sender_id") or {}

    message_id: str = message.get("message_id", "")
    chat_id: str = message.get("chat_id", "")
    chat_type: str = message.get("chat_type", "p2p")
    message_type: str = message.get("message_type", "")
    content_raw: str = message.get("content", "")

    open_id: Optional[str] = sender_id_dict.get("open_id")
    user_id: Optional[str] = sender_id_dict.get("user_id") or sender_id_dict.get("open_id")

    text = _extract_text(message_type, content_raw, [])

    return FeishuMessageEvent(
        trace_id=trace_id,
        event_id=event_id,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        open_id=open_id,
        user_id=user_id,
        text=text,
    )


def _extract_text(message_type: str, content_raw: str, mentions: Any) -> str:
    if message_type != "text":
        return "暂不支持该消息类型。"
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) and content_raw else {}
        text = content.get("text", "").strip()
        text = _strip_mentions(text, mentions)
        return text
    except Exception:
        return str(content_raw).strip()


def _strip_mentions(text: str, mentions: Any) -> str:
    """Remove @mention placeholders (e.g. @_user_1) from text."""
    if not text:
        return text
    if mentions:
        for mention in mentions:
            if isinstance(mention, dict):
                key = mention.get("key", "")
            else:
                key = getattr(mention, "key", "") or ""
            if key:
                text = text.replace(key, "").strip()
    # strip leading standalone @xxx tokens that remain (common in group chat)
    text = re.sub(r"^@\S+\s*", "", text).strip()
    return text

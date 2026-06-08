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
    message_create_time = _to_int(_safe_get(message, "create_time"))
    content_raw: str = _safe_get(message, "content", default="") or ""
    mentions = _safe_get(message, "mentions", default=[]) or []

    open_id: Optional[str] = _safe_get(sender_id_obj, "open_id")
    user_id: Optional[str] = _safe_get(sender_id_obj, "user_id")

    content = _parse_content(content_raw)
    text = _extract_text(message_type, content, mentions)
    attachments = _extract_attachments(message_type, content)

    return FeishuMessageEvent(
        trace_id=trace_id,
        event_id=event_id,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        open_id=open_id,
        user_id=user_id,
        text=text,
        message_type=message_type,
        message_create_time=message_create_time,
        content=content,
        attachments=attachments,
        raw_event={"message": _safe_dump(message)},
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
    message_create_time = _to_int(message.get("create_time"))
    content_raw: str = message.get("content", "")

    open_id: Optional[str] = sender_id_dict.get("open_id")
    user_id: Optional[str] = sender_id_dict.get("user_id") or sender_id_dict.get("open_id")

    content = _parse_content(content_raw)
    text = _extract_text(message_type, content, [])
    attachments = _extract_attachments(message_type, content)

    return FeishuMessageEvent(
        trace_id=trace_id,
        event_id=event_id,
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        open_id=open_id,
        user_id=user_id,
        text=text,
        message_type=message_type,
        message_create_time=message_create_time,
        content=content,
        attachments=attachments,
        raw_event=raw,
    )


def _parse_content(content_raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content_raw) if isinstance(content_raw, str) and content_raw else {}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except Exception:
        return {"raw": str(content_raw)}


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_text(message_type: str, content: dict[str, Any], mentions: Any) -> str:
    try:
        if message_type == "text":
            text = content.get("text", "").strip()
        elif message_type == "post":
            text = _extract_post_text(content)
        elif message_type in {"image", "file", "media", "video", "audio"}:
            text = ""
        else:
            text = f"收到 {message_type or '未知'} 类型消息。"
        text = _strip_mentions(text, mentions)
        return text
    except Exception:
        return str(content.get("raw", "")).strip()


def _extract_post_text(content: dict[str, Any]) -> str:
    title = content.get("title") or ""
    lines: list[str] = [str(title)] if title else []
    for blocks in (content.get("content") or []):
        for item in blocks:
            if isinstance(item, dict) and item.get("tag") == "text":
                lines.append(str(item.get("text", "")))
    return "\n".join(part.strip() for part in lines if part and part.strip()).strip()


def _extract_attachments(message_type: str, content: dict[str, Any]) -> list[dict[str, Any]]:
    if message_type == "post":
        return _extract_post_attachments(content)
    if message_type not in {"image", "file", "media", "video", "audio"}:
        return []
    key = content.get("image_key") or content.get("file_key") or content.get("media_key") or content.get("audio_key")
    if not key:
        return []
    file_name = (
        content.get("file_name")
        or content.get("name")
        or content.get("title")
        or _default_attachment_name(message_type)
    )
    return [{
        "type": message_type,
        "resource_key": key,
        "file_name": PathishName.clean(str(file_name)),
        "size": content.get("size"),
        "mime": content.get("mime"),
    }]


def _extract_post_attachments(content: dict[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for item in _walk_post_items(content.get("content") or []):
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").lower()
        image_key = item.get("image_key") or item.get("img_key")
        file_key = item.get("file_key")
        if tag in {"img", "image"} and image_key:
            attachments.append({
                "type": "image",
                "resource_key": image_key,
                "file_name": PathishName.clean(str(item.get("file_name") or item.get("name") or "feishu_image.png")),
                "size": item.get("size"),
                "mime": item.get("mime"),
            })
        elif tag == "file" and file_key:
            attachments.append({
                "type": "file",
                "resource_key": file_key,
                "file_name": PathishName.clean(str(item.get("file_name") or item.get("name") or "feishu_file.bin")),
                "size": item.get("size"),
                "mime": item.get("mime"),
            })
    return attachments


def _walk_post_items(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_post_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_post_items(child)


class PathishName:
    @staticmethod
    def clean(name: str) -> str:
        name = name.replace("\\", "/").split("/")[-1].strip()
        return name or "feishu_attachment"


def _default_attachment_name(message_type: str) -> str:
    defaults = {
        "image": "feishu_image.png",
        "video": "feishu_video.mp4",
        "media": "feishu_media.bin",
        "audio": "feishu_audio.mp3",
        "file": "feishu_file.bin",
    }
    return defaults.get(message_type, "feishu_attachment.bin")


def _safe_dump(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    result: dict[str, Any] = {}
    for key in ("message_id", "chat_id", "chat_type", "message_type", "content"):
        value = getattr(obj, key, None)
        if value is not None:
            result[key] = value
    return result


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

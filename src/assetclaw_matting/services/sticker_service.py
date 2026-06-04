from __future__ import annotations

import random
from pathlib import Path
from typing import Any


def sticker_status() -> dict[str, Any]:
    from assetclaw_matting.config import settings

    items = _collect_stickers()
    return {
        "ok": True,
        "enabled": bool(settings.bot_emotional_replies_enabled),
        "directory": str(settings.bot_sticker_dir),
        "directory_exists": settings.bot_sticker_dir.exists(),
        "probability": float(settings.bot_sticker_probability),
        "max_bytes": int(settings.bot_sticker_max_bytes),
        "extensions": settings.bot_sticker_extensions_list,
        "count": len(items),
        "sample": [str(path) for path in items[:8]],
    }


def choose_sticker(message_text: str = "", reply_text: str = "", force: bool = False) -> Path | None:
    from assetclaw_matting.config import settings

    if not force and not settings.bot_emotional_replies_enabled:
        return None
    if not force and not _should_send(reply_text):
        return None
    if not force:
        probability = max(0.0, min(float(settings.bot_sticker_probability), 1.0))
        if random.random() > probability:
            return None
    items = _collect_stickers()
    if not items:
        return None
    preferred = _prefer_animated(message_text, reply_text, items)
    return random.choice(preferred or items)


def send_sticker_to_chat(chat_id: str, message_text: str = "", reply_text: str = "", force: bool = False) -> dict[str, Any]:
    if not chat_id:
        return {"ok": False, "sent": False, "reason": "missing chat_id"}
    path = choose_sticker(message_text=message_text, reply_text=reply_text, force=force)
    if not path:
        return {"ok": True, "sent": False, "reason": "not_selected"}
    from assetclaw_matting.feishu.client import feishu_client

    feishu_client.send_image_to_chat(chat_id, path)
    return {"ok": True, "sent": True, "path": str(path)}


def _collect_stickers() -> list[Path]:
    from assetclaw_matting.config import settings

    root = settings.bot_sticker_dir
    if not root.exists() or not root.is_dir():
        return []
    exts = set(settings.bot_sticker_extensions_list or [".png", ".gif"])
    max_bytes = max(1, int(settings.bot_sticker_max_bytes or 1))
    items: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        items.append(path)
    return sorted(items, key=lambda item: str(item).lower())


def _should_send(reply_text: str) -> bool:
    text = (reply_text or "").strip()
    if not text:
        return False
    if text in {"收到，处理中。", "确认收到，正在执行。"}:
        return False
    if text.startswith("收到，处理中"):
        return False
    if text.startswith("请确认是否"):
        return False
    if "回复：确认执行" in text:
        return False
    return True


def _prefer_animated(message_text: str, reply_text: str, items: list[Path]) -> list[Path]:
    text = f"{message_text}\n{reply_text}".lower()
    animated = [path for path in items if path.suffix.lower() == ".gif"]
    still = [path for path in items if path.suffix.lower() == ".png"]
    lively_words = ("完成", "好了", "成功", "谢谢", "太好了", "开跑", "启动", "继续", "ok", "nice")
    heavy_words = ("失败", "错误", "卡", "没开始", "终止", "取消", "蠢", "糟糕", "不对")
    if animated and any(word in text for word in lively_words):
        return animated
    if still and any(word in text for word in heavy_words):
        return still
    return []

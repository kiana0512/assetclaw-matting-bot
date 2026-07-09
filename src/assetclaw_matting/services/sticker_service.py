from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageSequence

_LAST_SENT_AT_BY_CHAT: dict[str, float] = {}


def sticker_status() -> dict[str, Any]:
    from assetclaw_matting.config import settings

    items = _collect_stickers()
    return {
        "ok": True,
        "enabled": bool(settings.bot_emotional_replies_enabled),
        "directory": str(settings.bot_sticker_dir),
        "directory_exists": settings.bot_sticker_dir.exists(),
        "probability": float(settings.bot_sticker_probability),
        "cooldown_seconds": int(settings.bot_sticker_cooldown_seconds),
        "max_bytes": int(settings.bot_sticker_max_bytes),
        "send_max_px": int(settings.bot_sticker_send_max_px),
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
    if not force and _is_in_cooldown(chat_id):
        return {"ok": True, "sent": False, "reason": "cooldown"}
    path = choose_sticker(message_text=message_text, reply_text=reply_text, force=force)
    if not path:
        return {"ok": True, "sent": False, "reason": "not_selected"}
    from assetclaw_matting.feishu.client import feishu_client

    send_path = _prepare_sticker_for_send(path)
    feishu_client.send_image_to_chat(chat_id, send_path)
    if not force:
        _LAST_SENT_AT_BY_CHAT[chat_id] = time.time()
    return {"ok": True, "sent": True, "path": str(send_path), "source_path": str(path)}


def _prepare_sticker_for_send(path: Path) -> Path:
    from assetclaw_matting.config import settings

    max_px = max(64, min(int(settings.bot_sticker_send_max_px or 240), 1024))
    cache_dir = settings.storage_dir / "sticker_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    target = cache_dir / f"{path.stem}_{max_px}px{suffix if suffix == '.gif' else '.png'}"
    try:
        if target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
            return target
        with Image.open(path) as img:
            if suffix == ".gif":
                _save_resized_gif(img, target, max_px)
            else:
                resized = img.convert("RGBA")
                resized.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
                resized.save(target)
        return target
    except Exception:
        return path


def _save_resized_gif(img: Image.Image, target: Path, max_px: int) -> None:
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(img):
        resized = frame.convert("RGBA")
        resized.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        frames.append(resized)
        durations.append(int(frame.info.get("duration", img.info.get("duration", 80)) or 80))
    if not frames:
        raise ValueError("gif has no frames")
    frames[0].save(
        target,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=int(img.info.get("loop", 0) or 0),
        disposal=2,
    )


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
    if text in {"收到，处理中。", "确认收到，正在执行。", "完成。"}:
        return False
    quiet_prefixes = (
        "收到",
        "确认收到",
        "请确认是否",
        "动画处理进度",
        "图片处理进度",
        "动画处理任务",
        "图片处理任务",
        "动画处理已",
        "图片处理已",
        "抽帧完成",
        "ComfyUI 抠图完成",
        "Cherry 后处理完成",
        "开始抽帧",
        "开始 ComfyUI",
        "开始 Cherry",
        "开始打包",
    )
    if text.startswith(quiet_prefixes):
        return False
    quiet_markers = (
        "确认执行",
        "取消：",
        "状态：",
        "抠图：",
        "后处理：",
        "已抽帧：",
        "正在发送 zip",
    )
    if any(marker in text for marker in quiet_markers):
        return False
    return True


def _is_in_cooldown(chat_id: str) -> bool:
    from assetclaw_matting.config import settings

    cooldown = max(0, int(getattr(settings, "bot_sticker_cooldown_seconds", 0) or 0))
    if cooldown <= 0:
        return False
    last_sent_at = _LAST_SENT_AT_BY_CHAT.get(chat_id)
    return bool(last_sent_at and time.time() - last_sent_at < cooldown)


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

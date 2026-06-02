from __future__ import annotations

import re
from pathlib import Path

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall
from assetclaw_matting.skills.media_skills import IMAGE_EXTS, VIDEO_EXTS


def answer_recent_image_question(message: BrainMessage) -> str | None:
    text = message.text.strip()
    if not _asks_about_recent_image(text):
        return None
    path = _recent_image_path(message.conversation_id)
    if path:
        return f"记得，上一张图是：{Path(path).name}"
    return "我这边没存到上一张图。你再发一次，我会接着处理。"


def plan_multimodal_task(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    attachments = [item for item in message.attachments if item.get("local_path")]
    if not attachments:
        return None

    text = message.text.strip()
    first = attachments[0]
    path = str(first["local_path"])
    suffix = Path(path).suffix.lower()

    if _looks_like_save_or_copy(text):
        dst_dir = _extract_destination_dir(text)
        if dst_dir:
            return (
                [ToolCall(skill="file.copy_as", arguments={"src_path": path, "dst_dir": dst_dir, "new_name": Path(path).name})],
                f"保存附件到 {dst_dir}",
            )

    if _looks_like_preview(text):
        if suffix in IMAGE_EXTS:
            return [ToolCall(skill="feishu.send_image", arguments={"path": path})], "用图片形式发回飞书"
        return [ToolCall(skill="feishu.send_file", arguments={"path": path})], "用文件形式发回飞书"

    if _looks_like_info(text):
        if suffix in IMAGE_EXTS:
            return [ToolCall(skill="image.info", arguments={"path": path})], "查看图片信息"
        return [ToolCall(skill="file.info", arguments={"path": path})], "查看附件信息"

    if not text or text in {"收到", "好的", "ok", "OK"}:
        kind = "图片" if suffix in IMAGE_EXTS else "视频" if suffix in VIDEO_EXTS else "文件"
        return [], (
            f"收到{kind}：{Path(path).name}\n"
            f"已保存到：{path}\n"
            "你想让我怎么处理？可以说：保存到 E:\\images、查看信息、预览发回、或者分析内容。"
        )

    return None


def _recent_image_path(conversation_id: str) -> str | None:
    if not conversation_id:
        return None
    from assetclaw_matting.db.repos import list_memory_notes

    for item in list_memory_notes(conversation_id, limit=20):
        if item.get("key") == "last_image_path":
            path = str(item.get("value") or "")
            if path and Path(path).exists() and Path(path).suffix.lower() in IMAGE_EXTS:
                return path
    return None


def _asks_about_recent_image(text: str) -> bool:
    if not any(kw in text for kw in ("图片", "图", "照片", "截图")):
        return False
    return any(
        kw in text
        for kw in (
            "收到",
            "有没有",
            "有收到",
            "看到了",
            "记得",
            "还记得",
            "之前发",
            "刚刚发",
            "上一张",
            "上张",
            "最近",
        )
    )


def _looks_like_save_or_copy(text: str) -> bool:
    return any(kw in text for kw in ("保存", "存到", "复制到", "放到"))


def _looks_like_preview(text: str) -> bool:
    return any(kw in text for kw in ("发给我", "发回来", "展示", "预览", "图片形式", "直接显示"))


def _looks_like_info(text: str) -> bool:
    return any(kw in text for kw in ("信息", "尺寸", "大小", "分辨率", "格式"))


def _extract_destination_dir(text: str) -> str | None:
    match = re.search(r"([DEFdef]:\\[^\s，。]*)", text)
    if match:
        return match.group(1)
    normalized = text.lower().replace(" ", "")
    for drive in ("d", "e", "f"):
        if f"{drive}盘" in normalized:
            return f"{drive.upper()}:\\"
    return None

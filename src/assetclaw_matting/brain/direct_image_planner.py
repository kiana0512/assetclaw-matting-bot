from __future__ import annotations

from pathlib import Path
from typing import Any

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall
from assetclaw_matting.skills.media_skills import IMAGE_EXTS


STATUS_WORDS = ("图片处理进度", "图处理进度", "这张图处理", "这个图片处理", "直传图片进度")


def plan_direct_image_task(message: BrainMessage) -> tuple[list[ToolCall], str] | tuple[None, str] | None:
    text = (message.text or "").strip()
    if _asks_status(text):
        return [ToolCall(skill="direct_image.status", arguments={})], "direct image status route"

    images = _image_attachments(message.attachments)
    if not images:
        return None
    missing = [item for item in images if not item.get("local_path")]
    if missing:
        return None, "我收到了图片附件，但还没有拿到本地文件，等下载完成后再处理。"

    paths = [str(item["local_path"]) for item in images if item.get("local_path")]
    names = [str(item.get("file_name") or Path(path).name) for item, path in zip(images, paths)]
    return (
        [
            ToolCall(
                skill="direct_image.start",
                arguments={
                    "image_paths": paths,
                    "source_names": names,
                    "run_label": "、".join(names[:3]),
                },
            )
        ],
        "direct Feishu image attachment route",
    )


def _asks_status(text: str) -> bool:
    if not text:
        return False
    return any(word in text for word in STATUS_WORDS) or (
        "处理进度" in text and any(word in text for word in ("图片", "图"))
    )


def _image_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images = []
    for item in items or []:
        raw_type = str(item.get("type") or "").lower()
        path = str(item.get("local_path") or item.get("file_name") or "")
        suffix = Path(path).suffix.lower()
        if raw_type == "image" or suffix in IMAGE_EXTS:
            images.append(item)
    return images

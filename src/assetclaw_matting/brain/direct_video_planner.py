from __future__ import annotations

from pathlib import Path
from typing import Any

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall
from assetclaw_matting.skills.media_skills import VIDEO_EXTS


START_WORDS = ("动画处理", "开始处理", "处理视频", "处理动画", "抽帧", "抠图", "后处理", "开始")
STATUS_WORDS = (
    "动画处理进度",
    "视频处理进度",
    "直传视频进度",
    "这个视频处理",
    "这个动画处理",
    "进度如何",
    "进度怎么样",
    "进度咋样",
    "到哪了",
    "处理到哪",
)
RESEND_WORDS = ("重发zip", "重发 zip", "重新发zip", "重新发 zip", "发回zip", "发回 zip", "zip再发", "zip 再发")
LIST_WORDS = ("任务列表", "进度列表", "全部进度", "这批任务", "六个任务", "6个任务", "所有视频进度")


def plan_direct_video_task(message: BrainMessage) -> tuple[list[ToolCall], str] | tuple[None, str] | None:
    text = (message.text or "").strip()
    if _asks_resend_zip(text):
        return [ToolCall(skill="direct_video.resend_zip", arguments={})], "direct video resend zip route"
    has_specific_batch_reference = any(word in text for word in ("这批任务", "六个任务", "6个任务", "所有视频进度"))
    has_video_context = any(word in text for word in ("视频", "动画", "直传"))
    if any(word in text for word in LIST_WORDS) and (has_specific_batch_reference or has_video_context):
        return [ToolCall(skill="direct_video.list", arguments={"limit": 10, "include_finished": True})], "direct video list route"
    if _asks_status(text):
        return [ToolCall(skill="direct_video.status", arguments={})], "direct video status route"

    videos = _video_attachments(message.attachments)
    if not videos:
        return None
    compressed = [item for item in videos if _is_feishu_media_video(item)]
    if compressed:
        return None, (
            "这个是飞书“视频”消息，飞书会转码压缩，机器人拿到的可能不是原始画质。\n"
            "为了保证收到多大就处理多大，请把 mp4/mov 当作“文件”发送，不要用视频入口发送。"
        )
    if text and not any(word in text for word in START_WORDS):
        return None

    missing = [item for item in videos if not item.get("local_path")]
    if missing:
        return None, "我收到了视频附件，但还没有拿到本地文件，等下载完成后再处理。"
    paths = [str(item["local_path"]) for item in videos if item.get("local_path")]
    names = [str(item.get("file_name") or Path(path).name) for item, path in zip(videos, paths)]
    return (
        [
            ToolCall(
                skill="direct_video.start",
                arguments={
                    "video_paths": paths,
                    "source_names": names,
                    "run_label": "、".join(names[:3]),
                },
            )
        ],
        "direct Feishu video attachment route",
    )


def _asks_status(text: str) -> bool:
    if not text:
        return False
    return any(word in text for word in STATUS_WORDS) or (
        "处理进度" in text and any(word in text for word in ("视频", "直传"))
    )


def _asks_resend_zip(text: str) -> bool:
    normalized = text.lower().replace(" ", "")
    return any(word.replace(" ", "") in normalized for word in RESEND_WORDS)


def _video_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    videos = []
    for item in items or []:
        raw_type = str(item.get("type") or "").lower()
        path = str(item.get("local_path") or item.get("file_name") or "")
        suffix = Path(path).suffix.lower()
        if raw_type in {"video", "media"} or suffix in VIDEO_EXTS:
            videos.append(item)
    return videos


def _is_feishu_media_video(item: dict[str, Any]) -> bool:
    source_type = str(item.get("source_message_type") or "").lower()
    raw_type = str(item.get("type") or "").lower()
    return source_type in {"media", "video"} or raw_type == "media"

from __future__ import annotations

import re

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall


TEXT_TASK_HINTS = (
    "润色",
    "改写",
    "重写",
    "总结",
    "摘要",
    "整理成",
    "整理一下这段",
    "提炼",
    "提取要点",
    "列成要点",
    "优化表达",
    "自然一点",
    "正式一点",
    "口语一点",
    "飞书文档",
)


def plan_text_task(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    text = (message.text or "").strip()
    if not text or not _looks_like_text_task(text):
        return None
    content = _extract_content(text)
    if not content:
        return None
    instruction = _extract_instruction(text, content)
    return (
        [
            ToolCall(
                skill="text.process",
                arguments={
                    "text": content,
                    "instruction": instruction,
                    "style": "natural",
                },
            )
        ],
        "处理纯文本任务",
    )


def _looks_like_text_task(text: str) -> bool:
    if any(marker in text for marker in ("：", ":", "\n")) and any(hint in text for hint in TEXT_TASK_HINTS):
        return not _looks_like_machine_task(text)
    return False


def _looks_like_machine_task(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"[A-Za-z]:\\|\\\\|/[\w.-]", text):
        return True
    return any(
        kw in lowered or kw in text
        for kw in (
            "comfyui",
            "cherry",
            "p4",
            "gpu",
            "文件",
            "目录",
            "路径",
            "任务",
            "队列",
            "抽帧",
            "抠图",
            "平滑",
            "删除",
            "移动",
            "复制",
            "启动",
            "开始",
            "终止",
        )
    )


def _extract_content(text: str) -> str:
    for sep in ("：", ":", "\n"):
        if sep in text:
            tail = text.split(sep, 1)[1].strip()
            if tail:
                return tail
    return ""


def _extract_instruction(text: str, content: str) -> str:
    head = text[: max(0, text.find(content))].strip(" ：:\n")
    return head or "处理这段文字"

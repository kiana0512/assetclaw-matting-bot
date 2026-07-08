from __future__ import annotations

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall


def plan_matting_pipeline_task(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    text = (message.text or "").strip()
    if not _mentions_pipeline(text):
        return None
    if any(kw in text for kw in ("更新", "拉最新", "同步最新", "升级", "pull", "update")):
        return [ToolCall(skill="matting_pipeline.update", arguments={})], "matting pipeline update route"
    if any(kw in text for kw in ("验证", "检查", "校验", "verify", "有没有问题", "是否正常")):
        return [ToolCall(skill="matting_pipeline.verify", arguments={})], "matting pipeline verify route"
    return [ToolCall(skill="matting_pipeline.status", arguments={})], "matting pipeline status route"


def _mentions_pipeline(text: str) -> bool:
    lowered = text.lower()
    if "imageclip" in lowered:
        return True
    return any(
        kw in text
        for kw in (
            "抠图管线",
            "抠图工作流",
            "管线版本",
            "工作流版本",
            "当前管线",
            "现在用的管线",
            "现在用的工作流",
            "comfyui管线",
            "ComfyUI管线",
            "ComfyUI 工作流版本",
            "Cherry_lizi",
        )
    )

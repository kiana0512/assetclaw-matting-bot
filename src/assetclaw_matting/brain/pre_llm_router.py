from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol

from assetclaw_matting.brain.conversation_recall import answer_recent_question
from assetclaw_matting.brain.direct_image_planner import plan_direct_image_task
from assetclaw_matting.brain.direct_video_planner import plan_direct_video_task
from assetclaw_matting.brain.file_task_planner import plan_file_task
from assetclaw_matting.brain.life_planner import plan_life_task
from assetclaw_matting.brain.matting_pipeline_planner import plan_matting_pipeline_task
from assetclaw_matting.brain.multimodal_planner import answer_recent_image_question, plan_multimodal_task
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse, ToolCall
from assetclaw_matting.brain.speech_planner import handle_voice_capability_question, handle_voice_message, handle_voice_reply_mode
from assetclaw_matting.brain.text_planner import plan_text_task
from assetclaw_matting.brain.translation_planner import plan_translation_task


class PreRouterProvider(Protocol):
    name: str

    def execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        conversation_id: str = "",
        user_id: str = "",
    ) -> list[dict]:
        ...

    def log_message(self, message: BrainMessage, response: BrainResponse, raw: dict | None = None) -> None:
        ...


def handle_pre_llm_message(provider: PreRouterProvider, message: BrainMessage) -> BrainResponse | None:
    text = message.text.strip()

    task_overview = _plan_task_overview(message)
    if task_overview:
        return _planned_response(provider, message, task_overview)

    direct_progress = _plan_direct_media_progress(message)
    if direct_progress:
        return _planned_response(provider, message, direct_progress)

    direct_video = plan_direct_video_task(message)
    if direct_video:
        return _planned_response(provider, message, direct_video)

    direct_image = plan_direct_image_task(message)
    if direct_image:
        return _planned_response(provider, message, direct_image)

    matting_pipeline = plan_matting_pipeline_task(message)
    if matting_pipeline:
        return _planned_response(provider, message, matting_pipeline)

    animation_flow = _plan_animation_flow(message)
    if animation_flow:
        return _planned_response(provider, message, animation_flow)

    voice_mode_response = handle_voice_reply_mode(provider, message)
    if voice_mode_response:
        return voice_mode_response

    voice_capability_response = handle_voice_capability_question(provider, message)
    if voice_capability_response:
        return voice_capability_response

    voice_response = handle_voice_message(provider, message)
    if voice_response:
        return voice_response

    image_answer = answer_recent_image_question(message)
    if image_answer:
        return _text_response(provider, message, image_answer)

    translated = plan_translation_task(message)
    if translated:
        return _planned_response(provider, message, translated)

    text_task = plan_text_task(message)
    if text_task:
        return _planned_response(provider, message, text_task)

    multimodal = plan_multimodal_task(message)
    if multimodal:
        return _planned_response(provider, message, multimodal)

    recalled = answer_recent_question(text, message.conversation_id)
    if recalled:
        return _text_response(provider, message, recalled)

    life = plan_life_task(message)
    if life:
        return _planned_response(provider, message, life)

    planned = plan_file_task(message)
    if planned:
        return _planned_response(provider, message, planned)

    return None


def _plan_task_overview(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    """Understand common task-question paraphrases before component planners run."""
    text = message.text.strip()
    normalized = "".join(text.split())
    lowered = normalized.lower()
    if not normalized or any(word in normalized for word in ("取消", "终止", "停止", "开始", "启动", "继续", "恢复")):
        return None
    if any(word in lowered for word in ("comfyui", "comfy_", "cherry", "frame_")) or any(
        word in normalized for word in ("抠图子任务", "后处理子任务", "抽帧子任务")
    ):
        return None
    # Preserve the established shorthand for a previously discussed six-video batch.
    if any(word in normalized for word in ("六个任务", "6个任务", "这批任务")):
        return None

    parent_id = re.search(
        r"(?<![A-Za-z0-9_])(?:IMG|VID|AFLOW)_[A-Z0-9]+(?![A-Za-z0-9_])",
        normalized,
        flags=re.IGNORECASE,
    )
    image_scope = any(
        word in normalized
        for word in (
            "直传图片", "图片直传", "图片直发", "直发图片", "图片直接发", "直接发图片", "图片任务", "图像任务", "图片那批",
        )
    )
    video_scope = any(
        word in normalized
        for word in (
            "直传视频", "视频直传", "视频直发", "直发视频", "视频直接发", "直接发视频", "视频任务", "视频那批",
        )
    )
    flow_scope = any(
        word in lowered
        for word in ("aflow",)
    ) or any(
        word in normalized
        for word in (
            "飞书动画",
            "完整动画流程",
            "动画全流程",
            "飞书下载任务",
            "表格下载任务",
            "飞书视频文件下载",
            "表格视频处理",
            "飞书那批",
            "表格那批",
            "下载抽帧",
        )
    )
    question_words = (
        "进度", "列表", "哪些", "有什么", "现在", "当前", "全部", "所有", "汇总", "情况", "怎么样", "如何", "到哪", "几个", "多少", "呢",
    )
    task_query = "任务" in normalized and any(
        word in normalized
        for word in question_words
    )
    scoped_question = (image_scope or video_scope or flow_scope) and any(word in normalized for word in question_words)
    natural_task_query = any(
        phrase in normalized
        for phrase in (
            "都在跑什么",
            "正在跑什么",
            "正在处理什么",
            "机器在干什么",
            "机器正在干嘛",
            "还有多少没做完",
            "还有几个没做完",
            "做完了哪些",
            "完成了哪些",
            "队列里有什么",
            "谁在排队",
            "排队情况",
            "工作进度",
            "处理情况",
        )
    )
    if not (parent_id or task_query or scoped_question or natural_task_query):
        return None

    if any(word in normalized for word in ("已完成", "完成了", "做完了", "处理完了", "已经完成")):
        view = "completed"
    elif any(word in normalized for word in ("排队", "队列", "等待处理", "还没开始")):
        view = "queue"
    elif any(word in normalized for word in ("全部", "所有", "历史", "已结束", "任务列表")):
        view = "list"
    elif any(word in normalized for word in ("正在跑的任务", "运行中的任务", "正在处理的任务")):
        view = "running"
    else:
        view = "active"
    args: dict[str, object] = {
        "scope": "all",
        "view": view,
    }
    if image_scope and not video_scope:
        args["scope"] = "image"
    elif video_scope and not image_scope:
        args["scope"] = "video"
    elif flow_scope:
        args["scope"] = "animation_flow"
    if parent_id:
        args["query"] = parent_id.group(0).upper()
    args["detail"] = any(word in normalized for word in ("详细", "明细", "具体"))
    return [ToolCall(skill="agent.task_overview", arguments=args)], "parent task overview route"


def _plan_direct_media_progress(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    text = message.text.strip()
    if not _is_generic_direct_progress_query(text):
        return None
    args = _direct_media_query_args(text)
    return [ToolCall(skill="agent.current_work", arguments=args)], "direct media progress overview route"


def _is_generic_direct_progress_query(text: str) -> bool:
    normalized = "".join((text or "").split())
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(word in lowered for word in ("comfyui", "cherry", "frame_", "comfy_")) or any(
        word in normalized for word in ("GPU", "显卡", "显存", "机器", "当前所有任务", "执行现场", "有哪些任务", "什么情况")
    ):
        return False
    if _looks_like_short_media_filter(normalized):
        return True
    return any(
        word in normalized
        for word in (
            "进度",
            "当前进度",
            "目前进度",
            "进度如何",
            "进度怎么样",
            "进度咋样",
            "任务跑到哪里",
            "任务跑到哪",
            "任务到哪里",
            "任务到哪",
            "动画任务",
            "视频任务",
            "图片任务",
            "任务汇总",
            "汇总",
            "具体信息",
            "详细信息",
            "任务详情",
            "详情",
            "刚刚上传",
            "刚上传",
            "刚才上传",
            "处理进度",
            "到哪了",
            "哪里了",
            "做到哪",
            "处理到哪",
            "跑到哪",
            "完成了吗",
            "好了吗",
        )
    )


def _direct_media_query_args(text: str) -> dict[str, object]:
    args: dict[str, object] = {"include_gpu": _mentions_gpu(text)}
    has_explicit_date = "今天" in text or "昨天" in text or _has_explicit_date(text)
    start, end = _extract_date_range(text)
    args["date_start"] = start
    args["date_end"] = end
    query = _extract_media_query(text)
    if not query and _looks_like_short_media_filter("".join(text.split())):
        query = _short_media_filter_query("".join(text.split()))
    if query:
        args["query"] = query
        if not has_explicit_date and _looks_like_file_or_run_query(query):
            args["date_start"] = None
            args["date_end"] = None
    if any(word in text for word in ("具体", "详细", "详情", "细节", "拆开", "每一步", "这个视频", "这个图片", "刚刚上传", "刚上传", "刚才上传")):
        args["detail"] = True
    if any(word in text for word in ("视频", "动画")) and not any(word in text for word in ("图片", "图像")):
        args["media_type"] = "video"
    elif any(word in text for word in ("图片", "图像", "这张图")) and not any(word in text for word in ("视频", "动画")):
        args["media_type"] = "image"
    return args


def _looks_like_file_or_run_query(query: str) -> bool:
    lowered = query.lower()
    return lowered.startswith(("vid_", "img_")) or lowered.endswith((".mp4", ".mov", ".avi", ".webm", ".png", ".jpg", ".jpeg", ".webp"))


def _looks_like_short_media_filter(normalized: str) -> bool:
    if not normalized:
        return False
    if normalized in {"待机", "思考"}:
        return True
    if 2 <= len(normalized) <= 16 and any(word in normalized for word in ("待机", "思考")):
        return True
    return False


def _short_media_filter_query(normalized: str) -> str:
    for word in ("待机", "思考"):
        if word in normalized:
            return word
    return normalized


def _mentions_gpu(text: str) -> bool:
    lowered = text.lower()
    return "gpu" in lowered or "nvidia-smi" in lowered or any(word in text for word in ("显卡", "显存", "温度", "功耗"))


def _extract_date_range(text: str) -> tuple[str, str]:
    today = datetime.now().date()
    if "昨天" in text:
        day = today.fromordinal(today.toordinal() - 1)
        return day.isoformat(), day.isoformat()
    if "今天" in text or not _has_explicit_date(text):
        return today.isoformat(), today.isoformat()
    matches = _date_mentions(text, today.year)
    if not matches:
        return today.isoformat(), today.isoformat()
    return min(matches), max(matches)


def _has_explicit_date(text: str) -> bool:
    return bool(re.search(r"(?<!\d)\d{3,4}(?!\d)|\d{1,2}\s*月\s*\d{1,2}\s*日?|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", text))


def _date_mentions(text: str, default_year: int) -> list[str]:
    dates: list[str] = []
    for match in re.finditer(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text):
        dates.extend(_make_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in re.finditer(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text):
        dates.extend(_make_date(default_year, int(match.group(1)), int(match.group(2))))
    for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", text):
        raw = match.group(1)
        if len(raw) == 3:
            month = int(raw[0])
            day = int(raw[1:])
        else:
            month = int(raw[:2])
            day = int(raw[2:])
        dates.extend(_make_date(default_year, month, day))
    return dates


def _make_date(year: int, month: int, day: int) -> list[str]:
    try:
        return [datetime(year, month, day).date().isoformat()]
    except ValueError:
        return []


def _extract_media_query(text: str) -> str:
    run_match = re.search(r"\b(?:VID|IMG)_[A-Fa-f0-9]{8,16}\b", text)
    if run_match:
        return run_match.group(0)
    file_match = re.search(r"([^\s，。|/\\]+(?:\s?\([^)]+\))?\.(?:mp4|mov|avi|webm|png|jpg|jpeg|webp))", text, re.IGNORECASE)
    if file_match:
        return file_match.group(1).strip(" ：:，。,.")
    if any(word in text for word in ("刚刚上传", "刚上传", "刚才上传", "刚刚发", "刚才发")):
        return ""
    return ""


def _timestamp_score(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _plan_animation_flow(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    from assetclaw_matting.brain.local_command_brain import LocalCommandBrain

    calls = LocalCommandBrain()._infer_tool_calls(message.text)
    if not calls:
        return None
    if any(str(call.skill).startswith("animation_flow.") for call in calls):
        return calls, "deterministic animation_flow route before LLM"
    return None


def _planned_response(
    provider: PreRouterProvider,
    message: BrainMessage,
    planned: tuple[list[ToolCall], str] | tuple[None, str],
) -> BrainResponse:
    tool_calls, planned_text = planned
    if not tool_calls:
        return _text_response(provider, message, planned_text)
    results = provider.execute_tool_calls(
        tool_calls,
        conversation_id=message.conversation_id,
        user_id=message.user_id,
    )
    response = BrainResponse(
        text=format_skill_results(results),
        tool_calls=tool_calls,
        raw={"deterministic_plan": planned_text, "skill_results": results},
        provider=provider.name,
    )
    provider.log_message(message, response)
    return response


def _text_response(provider: PreRouterProvider, message: BrainMessage, text: str) -> BrainResponse:
    response = BrainResponse(text=text, provider=provider.name)
    provider.log_message(message, response)
    return response

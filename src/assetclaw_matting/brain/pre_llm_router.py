from __future__ import annotations

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


def _plan_direct_media_progress(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    text = message.text.strip()
    if not _is_generic_direct_progress_query(text):
        return None
    from assetclaw_matting.skills import direct_image_skills, direct_video_skills

    candidates: list[tuple[str, dict]] = []
    for kind, status_fn in (("video", direct_video_skills.status), ("image", direct_image_skills.status)):
        try:
            payload = status_fn()
        except Exception:
            continue
        if payload.get("ok") and payload.get("run_id"):
            candidates.append((kind, payload))
    if not candidates:
        return None
    active = [item for item in candidates if str(item[1].get("status") or "") not in {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}]
    selected = max(active or candidates, key=lambda item: _timestamp_score(str(item[1].get("updated_at") or item[1].get("created_at") or "")))
    kind, payload = selected
    skill = "direct_video.status" if kind == "video" else "direct_image.status"
    return [ToolCall(skill=skill, arguments={"run_id": payload.get("run_id")})], f"latest direct {kind} progress route"


def _is_generic_direct_progress_query(text: str) -> bool:
    normalized = "".join((text or "").split())
    if not normalized:
        return False
    if any(word in normalized for word in ("GPU", "显卡", "显存", "机器", "当前所有任务", "执行现场", "有哪些任务", "什么情况")):
        return False
    return any(
        word in normalized
        for word in (
            "进度如何",
            "进度怎么样",
            "进度咋样",
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

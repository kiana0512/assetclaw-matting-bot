from __future__ import annotations

from typing import Protocol

from assetclaw_matting.brain.conversation_recall import answer_recent_question
from assetclaw_matting.brain.direct_image_planner import plan_direct_image_task
from assetclaw_matting.brain.direct_video_planner import plan_direct_video_task
from assetclaw_matting.brain.file_task_planner import plan_file_task
from assetclaw_matting.brain.life_planner import plan_life_task
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

    direct_video = plan_direct_video_task(message)
    if direct_video:
        return _planned_response(provider, message, direct_video)

    direct_image = plan_direct_image_task(message)
    if direct_image:
        return _planned_response(provider, message, direct_image)

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

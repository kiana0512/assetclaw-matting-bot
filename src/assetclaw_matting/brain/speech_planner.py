from __future__ import annotations

from pathlib import Path
import re

from assetclaw_matting.brain.schemas import BrainMessage, BrainResponse
from assetclaw_matting.skills.media_skills import VIDEO_EXTS
from assetclaw_matting.skills.speech_skills import AUDIO_EXTS, transcribe


VOICE_REPLY_KEY = "voice_reply_mode"
VOICE_REPLY_VALUE = "enabled"


def handle_voice_reply_mode(provider, message: BrainMessage) -> BrainResponse | None:
    command = _voice_reply_command(message.text)
    if not command:
        return None
    _set_voice_reply_mode(message.conversation_id, command == "on")
    if command == "on":
        text = "已开启语音回复。后面我会继续发文字，同时尽量把回复合成语音文件发回飞书。"
    else:
        text = "已关闭语音回复。现在恢复为只发文字。"
    response = BrainResponse(text=text, provider=provider.name)
    provider.log_message(message, response)
    return response


def handle_voice_capability_question(provider, message: BrainMessage) -> BrainResponse | None:
    if not _asks_voice_capability(message.text):
        return None
    text = (
        "可以听你的语音。你直接在飞书发语音，我会先用本地 ASR 转成文字，再按正常 Agent 逻辑处理；"
        "如果你说的是生产指令，也会继续转成对应工具调用。\n"
        "如果想让我也用语音回复你，说“开启语音回复”；想只收文字，说“关闭语音回复”或“只发文字”。"
    )
    response = BrainResponse(text=text, provider=provider.name)
    provider.log_message(message, response)
    return response


def voice_reply_enabled(conversation_id: str) -> bool:
    if not conversation_id:
        return False
    from assetclaw_matting.db.repos import list_memory_notes

    for note in list_memory_notes(conversation_id, limit=30):
        if note.get("key") == VOICE_REPLY_KEY:
            return str(note.get("value") or "") == VOICE_REPLY_VALUE
    return False


def handle_voice_message(provider, message: BrainMessage) -> BrainResponse | None:
    audio_item = _first_audio_item(message)
    if not audio_item:
        return None
    audio_path = str(audio_item.get("local_path") or "")
    if not audio_path:
        response = BrainResponse(
            text="我收到语音了，但这条语音还没下载到本地，暂时不能转文字。你可以再发一次，或检查飞书资源下载权限。",
            provider=provider.name,
            raw={"speech": {"ok": False, "error": "missing local_path", "attachment": audio_item}},
        )
        provider.log_message(message, response)
        return response

    try:
        payload = transcribe(audio_path, language="zh", prompt="飞书语音指令，可能包含文件、动画流程、ComfyUI、P4、GPU、任务状态等操作。")
    except Exception as exc:
        payload = {"ok": False, "audio_path": audio_path, "error": str(exc)}
    if not payload.get("ok"):
        response = BrainResponse(
            text=f"我收到语音了，但暂时没法转文字：{payload.get('error')}",
            provider=provider.name,
            raw={"speech": payload},
        )
        provider.log_message(message, response)
        return response

    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        response = BrainResponse(text="我收到语音了，但没有识别到清晰文字。", provider=provider.name, raw={"speech": payload})
        provider.log_message(message, response)
        return response

    prefix = message.text.strip()
    routed_text = f"{prefix}\n{transcript}".strip() if prefix else transcript
    replay = message.model_copy(update={"text": routed_text, "attachments": []})
    response = provider.handle_message(replay)
    response.raw = {**(response.raw or {}), "speech_transcript": transcript, "speech_engine": payload.get("engine")}
    if response.text:
        response.text = f"语音识别：{transcript}\n{response.text}"
    else:
        response.text = f"语音识别：{transcript}"
    provider.log_message(message, response, {"speech": payload, "routed_text": routed_text, "response": response.raw})
    return response


def _first_audio_item(message: BrainMessage) -> dict | None:
    for item in message.attachments:
        raw_type = str(item.get("type") or "").lower()
        if raw_type in {"video", "media"}:
            continue
        path = str(item.get("local_path") or "")
        name = str(item.get("file_name") or "")
        suffix = Path(path or name).suffix.lower()
        if suffix in VIDEO_EXTS:
            continue
        if raw_type in {"audio", "voice"} or suffix in AUDIO_EXTS:
            return item
    return None


def _voice_reply_command(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    if any(word in compact for word in ("开启语音回复", "打开语音回复", "进入语音模式", "用语音回复我", "以后语音回复")):
        return "on"
    if any(word in compact for word in ("关闭语音回复", "停止语音回复", "退出语音模式", "不要语音回复", "只发文字")):
        return "off"
    return ""


def _asks_voice_capability(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "").lower()
    if any(word in compact for word in ("可以听我的语音吗", "能听我的语音吗", "听我的语音", "发语音可以吗", "支持语音吗")):
        return True
    return "asr" in compact and any(word in compact for word in ("可以", "支持", "怎么用", "能用"))


def _set_voice_reply_mode(conversation_id: str, enabled: bool) -> None:
    if not conversation_id:
        return
    from assetclaw_matting.db.repos import upsert_memory_note

    upsert_memory_note(conversation_id, VOICE_REPLY_KEY, VOICE_REPLY_VALUE if enabled else "disabled", source="voice_reply_mode")

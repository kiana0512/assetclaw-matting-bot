from __future__ import annotations

import re
from pathlib import Path

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall
from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.speech_skills import AUDIO_EXTS


LANG_ALIASES = {
    "英文": "English",
    "英语": "English",
    "中文": "Chinese",
    "汉语": "Chinese",
    "日文": "Japanese",
    "日语": "Japanese",
    "韩文": "Korean",
    "韩语": "Korean",
    "法文": "French",
    "法语": "French",
    "德文": "German",
    "德语": "German",
    "西班牙语": "Spanish",
    "泰语": "Thai",
    "越南语": "Vietnamese",
    "印尼语": "Indonesian",
}


def plan_translation_task(message: BrainMessage) -> tuple[list[ToolCall], str] | None:
    text = message.text.strip()
    if any(_is_audio_attachment(item) for item in message.attachments):
        return None
    attachments = [item for item in message.attachments if item.get("local_path")]
    explicit_image = _first_image_path(attachments) or _extract_image_path_from_text(text)
    image = explicit_image
    if not image and (_references_image(text) or _looks_like_ocr(text) or not text):
        image = _recent_image_path(message.conversation_id)

    if image and not text:
        recent_task = _recent_image_text_task(message.conversation_id)
        if recent_task:
            task, target = recent_task
            if task == "ocr":
                return (
                    [ToolCall(skill="image.ocr", arguments={"image_path": image, "instruction": "提取图片文字"})],
                    "提取图片文字",
                )
            return (
                [ToolCall(skill="translate.image_text", arguments={
                    "image_path": image,
                    "target_language": _normalize_lang(target or "中文"),
                    "instruction": "识别并翻译图片文字",
                })],
                "翻译图片文字",
            )

    if message.attachments and not attachments and (not text or _looks_like_ocr(text) or _looks_like_translate(text)):
        return [], _download_failed_text(message.attachments)

    if not _looks_like_translate(text) and not _looks_like_ocr(text):
        return None

    target = _extract_target_language(text)
    if image:
        if _looks_like_ocr(text) and not target:
            return (
                [ToolCall(skill="image.ocr", arguments={"image_path": image, "instruction": text})],
                "提取图片文字",
            )
        return (
            [ToolCall(skill="translate.image_text", arguments={
                "image_path": image,
                "target_language": _normalize_lang(target or "中文"),
                "instruction": text,
            })],
            "翻译图片文字",
        )

    if _references_image(text) and (_looks_like_ocr(text) or target):
        return [], "我这边没拿到这张图。你再发一次，或发本地图片路径。"

    if _looks_like_ocr(text) and not target:
        return [], "可以，把图片发我，或者指定一张本地图片路径，我来提取文字。"

    content = _extract_text_to_translate(text)
    if not content:
        return [], "可以，把要翻译的文字发我，或者直接发图片并说要翻译成什么语言。"
    return (
        [ToolCall(skill="translate.text", arguments={
            "text": content,
            "target_language": _normalize_lang(target or "中文"),
            "style": "natural",
        })],
        "翻译文字",
    )


def _looks_like_translate(text: str) -> bool:
    lowered = text.lower()
    return "translate" in lowered or any(kw in text for kw in ("翻译", "译成", "转成", "改成")) and any(
        lang in text for lang in LANG_ALIASES
    )


def _looks_like_ocr(text: str) -> bool:
    lowered = text.lower()
    return "ocr" in lowered or any(
        kw in text
        for kw in (
            "提取文字",
            "提取文本",
            "识别文字",
            "识别文本",
            "图片里的字",
            "图片里的文字",
            "图片中的文字",
            "图里的字",
            "图里的文字",
            "图中文字",
            "图中的文字",
            "图片文字",
        )
    )


def _references_image(text: str) -> bool:
    return any(
        kw in text
        for kw in (
            "[图片]",
            "图片",
            "图里",
            "图中",
            "这张图",
            "这个图",
            "刚刚那张图",
        )
    )


def _download_failed_text(attachments: list[dict]) -> str:
    detail = "\n".join(str(item.get("error") or "") for item in attachments)
    if "99991672" in detail or "Access denied" in detail or "action_scope_required" in detail:
        return "图片拉不下来：飞书应用缺少消息资源读取权限。要开 im:message:readonly 或 im:message.history:readonly。"
    return "图片没下到本地，暂时不能 OCR。"


def _is_audio_attachment(item: dict) -> bool:
    raw_type = str(item.get("type") or "").lower()
    path = str(item.get("local_path") or "")
    name = str(item.get("file_name") or "")
    return raw_type in {"audio", "voice"} or Path(path or name).suffix.lower() in AUDIO_EXTS


def _extract_target_language(text: str) -> str | None:
    patterns = [
        r"(?:翻译|译|转|改)(?:成|为|到)\s*([A-Za-z\u4e00-\u9fff]+)",
        r"into\s+([A-Za-z]+)",
        r"to\s+([A-Za-z]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1).strip(" ：:，。,.")
            for alias in sorted(LANG_ALIASES, key=len, reverse=True):
                if raw.startswith(alias):
                    return alias
            return raw
    for alias in LANG_ALIASES:
        if alias in text:
            return alias
    return None


def _extract_text_to_translate(text: str) -> str:
    stripped = text.strip()
    for sep in ("：", ":", "\n"):
        if sep in stripped:
            tail = stripped.split(sep, 1)[1].strip()
            if tail:
                return tail
    question_tail = re.search(r"(?:吗|么|嘛)\s+(.+)$", stripped)
    if question_tail:
        return question_tail.group(1).strip(" ：:，。,.")
    quoted = re.search(r"[“\"'](.+?)[”\"']", stripped)
    if quoted:
        return quoted.group(1).strip()
    cleaned = re.sub(r"请?帮?我?把?", "", stripped)
    cleaned = re.sub(r"(翻译|译成|翻成|转成|改成)\s*[A-Za-z\u4e00-\u9fff]*", "", cleaned)
    return cleaned.strip(" ：:，。,.")


def _normalize_lang(value: str) -> str:
    return LANG_ALIASES.get(value, value)


def _first_image_path(attachments: list[dict]) -> str | None:
    for item in attachments:
        path = str(item.get("local_path") or "")
        if Path(path).suffix.lower() in IMAGE_EXTS:
            return path
    return None


def _extract_image_path_from_text(text: str) -> str | None:
    match = re.search(r"((?:[A-Za-z]:|\\\\)[^\s，。]*\.(?:png|jpg|jpeg|webp|bmp|gif|tif|tiff))", text, re.IGNORECASE)
    if match:
        return match.group(1)
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


def _recent_image_text_task(conversation_id: str) -> tuple[str, str | None] | None:
    if not conversation_id:
        return None
    from assetclaw_matting.db.repos import get_recent_brain_messages

    for item in reversed(get_recent_brain_messages(conversation_id, limit=6)):
        text = str(item.get("message_text") or "").strip()
        if not text:
            continue
        if _looks_like_translate(text) and _references_image(text):
            return "translate", _extract_target_language(text)
        if _looks_like_ocr(text) or ("OCR" in text.upper() and _references_image(text)):
            return "ocr", None
    return None

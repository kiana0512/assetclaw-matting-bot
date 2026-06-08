from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

import requests

from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.local_ocr import local_ocr_image
from assetclaw_matting.skills.security import validate_path


def translate_text(
    text: str,
    target_language: str,
    source_language: str | None = None,
    style: str = "natural",
) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("text is required")
    if not target_language.strip():
        raise ValueError("target_language is required")
    translated = _translate_text_best_effort(
        text,
        target_language=target_language,
        source_language=source_language,
        style=style,
    )
    return {
        "ok": True,
        "source_language": source_language or "auto",
        "target_language": target_language,
        "text": text,
        "translation": translated.strip(),
        "display_text": translated.strip(),
    }


def translate_image_text(
    image_path: str,
    target_language: str,
    source_language: str | None = None,
    instruction: str = "",
) -> dict[str, Any]:
    target = validate_path(image_path, must_exist=True)
    if not target.is_file() or target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("image_path must be a supported image file")
    if not target_language.strip():
        raise ValueError("target_language is required")
    prompt = (
        "Read all visible text in this image, then translate it. "
        "Return concise natural language. If useful, keep the structure as: 原文 / 翻译. "
        "Do not invent text that is not visible.\n"
        f"Source language: {source_language or 'auto'}\n"
        f"Target language: {target_language}\n"
    )
    if instruction:
        prompt += f"Extra instruction: {instruction}\n"
    try:
        translated = _complete_image(prompt, target)
    except RuntimeError as exc:
        if _is_llm_proxy_unconfigured(exc):
            return _translate_image_text_with_local_ocr(
                target,
                target_language=target_language,
                source_language=source_language,
                instruction=instruction,
            )
        raise
    _reject_instruction_only_result(translated, instruction)
    return {
        "ok": True,
        "image_path": str(target),
        "file_name": target.name,
        "source_language": source_language or "auto",
        "target_language": target_language,
        "translation": translated.strip(),
        "display_text": translated.strip(),
    }


def image_ocr(
    image_path: str,
    instruction: str = "",
) -> dict[str, Any]:
    target = validate_path(image_path, must_exist=True)
    if not target.is_file() or target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("image_path must be a supported image file")
    prompt = (
        "Extract all visible text from this image. Return only the recognized text. "
        "Keep line breaks when they help readability. Do not translate unless explicitly asked."
    )
    if instruction:
        prompt += f"\nExtra instruction: {instruction}"
    try:
        text = _complete_image(prompt, target)
    except RuntimeError as exc:
        if _is_llm_proxy_unconfigured(exc):
            local = _local_ocr_payload(target)
            if local:
                return local
            return _vision_unavailable_payload(target, "OCR")
        raise
    _reject_instruction_only_result(text, instruction)
    return {
        "ok": True,
        "image_path": str(target),
        "file_name": target.name,
        "text": text.strip(),
    }


def image_describe(
    image_path: str,
    instruction: str = "",
) -> dict[str, Any]:
    target = validate_path(image_path, must_exist=True)
    if not target.is_file() or target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("image_path must be a supported image file")
    prompt = (
        "Describe this image for the user in concise natural Chinese. "
        "Mention visible subjects, text if any, and the likely context. "
        "Do not invent details you cannot see."
    )
    if instruction:
        prompt += f"\nUser instruction: {instruction}"
    try:
        text = _complete_image(prompt, target)
    except RuntimeError as exc:
        if _is_llm_proxy_unconfigured(exc):
            return _image_describe_unavailable_payload(target, instruction)
        raise
    return {
        "ok": True,
        "image_path": str(target),
        "file_name": target.name,
        "text": text.strip(),
    }


def _complete_text(prompt: str) -> str:
    from assetclaw_matting.config import settings

    if not (settings.llm_proxy_enabled and settings.llm_proxy_base_url and settings.llm_proxy_api_key):
        raise RuntimeError("LLM Proxy is not configured")
    if settings.llm_proxy_openai_compatible:
        payload = {
            "model": settings.llm_proxy_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        data = _post_openai(payload)
        return data["choices"][0]["message"]["content"]
    payload = {
        "model": settings.llm_proxy_model,
        "max_tokens": 1600,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _post_anthropic(payload)
    return _extract_anthropic_text(data)


def _translate_text_best_effort(
    text: str,
    target_language: str,
    source_language: str | None = None,
    style: str = "natural",
) -> str:
    prompt = _translation_prompt(
        text,
        target_language=target_language,
        source_language=source_language,
        style=style,
    )
    try:
        translated = _complete_deepseek_text(prompt).strip()
        if translated:
            return translated
    except Exception:
        pass
    return _complete_text(prompt).strip()


def _translation_prompt(
    text: str,
    target_language: str,
    source_language: str | None = None,
    style: str = "natural",
) -> str:
    return (
        "Translate the following text. Return only the translation, in natural everyday language. "
        "Do not add explanations.\n"
        f"Source language: {source_language or 'auto'}\n"
        f"Target language: {target_language}\n"
        f"Style: {style or 'natural'}\n\n"
        f"{text}"
    )


def _is_llm_proxy_unconfigured(exc: RuntimeError) -> bool:
    return "LLM Proxy is not configured" in str(exc)


def _local_ocr_payload(target: Path) -> dict[str, Any] | None:
    local = local_ocr_image(target)
    if not local.get("available"):
        return None
    text = str(local.get("text") or "").strip()
    if not text:
        text = "本地 OCR 已运行，但没有识别到清晰文字。"
    return {
        "ok": True,
        "image_path": str(target),
        "file_name": target.name,
        "text": text,
        "engine": local.get("engine"),
        "vision_available": False,
        "local_ocr": True,
    }


def _translate_image_text_with_local_ocr(
    target: Path,
    target_language: str,
    source_language: str | None = None,
    instruction: str = "",
) -> dict[str, Any]:
    local = local_ocr_image(target)
    if not local.get("available"):
        payload = _vision_unavailable_payload(target, "图片文字翻译")
        payload["translation"] = payload.get("text", "")
        return payload
    ocr_text = str(local.get("text") or "").strip()
    if not ocr_text:
        return {
            "ok": True,
            "image_path": str(target),
            "file_name": target.name,
            "source_language": source_language or "auto",
            "target_language": target_language,
            "text": "本地 OCR 已运行，但没有识别到清晰文字。",
            "translation": "本地 OCR 已运行，但没有识别到清晰文字，所以暂时无法翻译。",
            "engine": local.get("engine"),
            "vision_available": False,
            "local_ocr": True,
        }
    translation = _translate_ocr_text_best_effort(
        ocr_text,
        target_language=target_language,
        source_language=source_language,
        instruction=instruction,
    )
    return {
        "ok": True,
        "image_path": str(target),
        "file_name": target.name,
        "source_language": source_language or "auto",
        "target_language": target_language,
        "text": ocr_text,
        "translation": translation,
        "display_text": translation,
        "engine": local.get("engine"),
        "vision_available": False,
        "local_ocr": True,
        "fallback": "local_ocr_text_translation",
    }


def _translate_ocr_text_best_effort(
    ocr_text: str,
    target_language: str,
    source_language: str | None = None,
    instruction: str = "",
) -> str:
    prompt = (
        "You are cleaning and translating noisy OCR from an image. The OCR text may contain duplicated words, "
        "broken spacing, mojibake, UI fragments, and recognition mistakes. First infer the coherent visible text, "
        "remove obvious OCR noise, merge broken lines, then output a natural-language result for the user. "
        "Do not dump raw OCR. If the image text is mostly a UI/document list, summarize the main meaning naturally. "
        "Return only the final natural result, no preface and no explanation.\n"
        f"Source language: {source_language or 'auto'}\n"
        f"Target language: {target_language}\n"
    )
    if instruction:
        prompt += f"User instruction: {instruction}\n"
    prompt += f"\nOCR text:\n{ocr_text}"
    try:
        translated = _complete_deepseek_text(prompt).strip()
        if translated:
            return translated
    except Exception:
        pass
    try:
        translated = translate_text(
            ocr_text,
            target_language=target_language,
            source_language=source_language,
            style="natural",
        ).get("translation", "")
        if str(translated).strip():
            return str(translated).strip()
    except Exception:
        pass
    return f"已识别到文字，但当前文本翻译引擎不可用，先给你原文：\n{ocr_text}"


def _vision_unavailable_payload(target: Path, task: str) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    missing = []
    if not settings.llm_proxy_enabled:
        missing.append("LLM_PROXY_ENABLED=true")
    if not settings.llm_proxy_api_key:
        missing.append("LLM_PROXY_API_KEY")
    if not settings.llm_proxy_base_url:
        missing.append("LLM_PROXY_BASE_URL")
    if not (settings.llm_proxy_complex_model or settings.llm_proxy_model):
        missing.append("LLM_PROXY_COMPLEX_MODEL 或 LLM_PROXY_MODEL")
    try:
        from PIL import Image

        with Image.open(target) as img:
            width, height = img.size
            mode = img.mode
            fmt = img.format or target.suffix.lstrip(".").upper()
    except Exception:
        width = height = 0
        mode = ""
        fmt = target.suffix.lstrip(".").upper()
    return {
        "ok": True,
        "image_path": str(target),
        "file_name": target.name,
        "text": (
            f"我收到图片了：{target.name}（{width}x{height}，{fmt}{('/' + mode) if mode else ''}）。"
            f"但当前视觉/OCR 模型没有配置，所以还不能真正做{task}。"
            + (f"缺少配置：{', '.join(missing)}。" if missing else "请检查 LLM Proxy 是否支持图片输入。")
        ),
        "vision_available": False,
        "needs_llm_proxy": True,
        "missing_config": missing,
        "width": width,
        "height": height,
        "format": fmt,
    }


def _image_describe_unavailable_payload(target: Path, instruction: str = "") -> dict[str, Any]:
    payload = _vision_unavailable_payload(target, "图片分析")
    local = local_ocr_image(target)
    if local.get("available"):
        text = str(local.get("text") or "").strip()
        if text:
            analysis = _analyze_ocr_text_with_deepseek(text, instruction, target, str(local.get("engine") or "local_ocr"))
            if analysis:
                return {
                    "ok": True,
                    "image_path": str(target),
                    "file_name": target.name,
                    "text": analysis,
                    "ocr_text": text,
                    "local_ocr": True,
                    "ocr_engine": local.get("engine"),
                    "analysis_engine": "deepseek",
                    "vision_available": False,
                }
            payload["text"] += f" 本地 OCR 先识别到这些文字：{text}。DeepSeek 文本分析暂时不可用，我先把识别结果给你。"
            payload["local_ocr_text"] = text
        else:
            payload["text"] += " 本地 OCR 已运行，但没有识别到清晰文字；当前没有视觉模型，所以还不能判断画面内容。"
        payload["local_ocr"] = True
        payload["engine"] = local.get("engine")
    return payload


def _analyze_ocr_text_with_deepseek(ocr_text: str, instruction: str, target: Path, ocr_engine: str) -> str:
    prompt = (
        "用户让你分析一张图片或表情包。当前不能直接看图，只能使用本地 OCR 识别出来的文字，"
        "再结合文件名和用户意图做谨慎分析。不要假装看到了画面细节；如果 OCR 信息不足，要明确说明。\n\n"
        f"用户要求：{instruction or '分析这张图片'}\n"
        f"文件名：{target.name}\n"
        f"OCR 引擎：{ocr_engine}\n"
        f"OCR 文字：{ocr_text}\n\n"
        "请用中文回复，口吻自然，先给结论，再补一句限制说明。控制在 120 字以内。"
    )
    try:
        return _complete_deepseek_text(prompt).strip()
    except Exception:
        return ""


def _complete_deepseek_text(prompt: str) -> str:
    from assetclaw_matting.config import settings

    if not (settings.deepseek_api_key and settings.deepseek_base_url):
        raise RuntimeError("DeepSeek is not configured")
    base = settings.deepseek_base_url.rstrip("/")
    endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    model = settings.deepseek_summary_model or settings.deepseek_model
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": float(settings.deepseek_temperature),
    }
    thinking_type = (settings.deepseek_thinking_type or "disabled").strip().lower()
    if thinking_type in {"enabled", "disabled"}:
        payload["thinking"] = {"type": thinking_type}
    response = requests.post(
        endpoint,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.deepseek_api_key}"},
        json=payload,
        timeout=settings.deepseek_timeout_seconds,
    )
    response.raise_for_status()
    choices = response.json().get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _complete_image(prompt: str, image_path: Path) -> str:
    from assetclaw_matting.config import settings

    if not (settings.llm_proxy_enabled and settings.llm_proxy_base_url and settings.llm_proxy_api_key):
        raise RuntimeError("LLM Proxy is not configured")
    mime = _image_mime(image_path)
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    model = settings.llm_proxy_complex_model or settings.llm_proxy_model
    if settings.llm_proxy_openai_compatible:
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                ],
            }],
            "temperature": 0,
        }
        data = _post_openai(payload)
        return data["choices"][0]["message"]["content"]
    payload = {
        "model": model,
        "max_tokens": 2000,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": encoded}},
            ],
        }],
    }
    data = _post_anthropic(payload)
    return _extract_anthropic_text(data)


def _endpoint() -> str:
    from assetclaw_matting.config import settings

    base = settings.llm_proxy_base_url.rstrip("/")
    if settings.llm_proxy_openai_compatible:
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    if base.endswith("/v1/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def _post_openai(payload: dict[str, Any]) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.llm_proxy_api_key}"}
    response = requests.post(_endpoint(), headers=headers, json=payload, timeout=settings.llm_proxy_timeout_seconds)
    if response.status_code == 401:
        headers = {"Content-Type": "application/json", "x-api-key": settings.llm_proxy_api_key}
        response = requests.post(_endpoint(), headers=headers, json=payload, timeout=settings.llm_proxy_timeout_seconds)
    _raise_with_body(response)
    return response.json()


def _post_anthropic(payload: dict[str, Any]) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    headers = {"anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    if settings.llm_proxy_auth_header == "x-api-key":
        headers["x-api-key"] = settings.llm_proxy_api_key
    else:
        headers["Authorization"] = f"Bearer {settings.llm_proxy_api_key}"
    response = requests.post(_endpoint(), headers=headers, json=payload, timeout=settings.llm_proxy_timeout_seconds)
    _raise_with_body(response)
    return response.json()


def _raise_with_body(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    detail = response.text[:1200] if response.text else response.reason
    raise requests.HTTPError(f"{response.status_code} LLM Proxy failed: {detail}", response=response)


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    parts = []
    for item in data.get("content", []):
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(part for part in parts if part).strip()


def _image_mime(path: Path) -> str:
    head = path.read_bytes()[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _reject_instruction_only_result(result: str, instruction: str) -> None:
    normalized = re.sub(r"[\s\W_]+", "", result.lower())
    bad_fragments = (
        "extractandtranslate",
        "extractthetext",
        "抽出してそして",
        "抽出して",
        "翻訳して",
    )
    if instruction and any(fragment in normalized for fragment in bad_fragments):
        raise RuntimeError("图片 OCR 失败：模型只处理了指令，没有读取到图片内容。请确认飞书图片已成功下载后再试。")

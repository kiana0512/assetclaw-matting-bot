from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

import requests

from assetclaw_matting.skills.media_skills import IMAGE_EXTS
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
    prompt = (
        "Translate the following text. Return only the translation, in natural everyday language. "
        "Do not add explanations.\n"
        f"Source language: {source_language or 'auto'}\n"
        f"Target language: {target_language}\n"
        f"Style: {style or 'natural'}\n\n"
        f"{text}"
    )
    translated = _complete_text(prompt)
    return {
        "ok": True,
        "source_language": source_language or "auto",
        "target_language": target_language,
        "text": text,
        "translation": translated.strip(),
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
    translated = _complete_image(prompt, target)
    _reject_instruction_only_result(translated, instruction)
    return {
        "ok": True,
        "image_path": str(target),
        "file_name": target.name,
        "source_language": source_language or "auto",
        "target_language": target_language,
        "translation": translated.strip(),
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
    text = _complete_image(prompt, target)
    _reject_instruction_only_result(text, instruction)
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

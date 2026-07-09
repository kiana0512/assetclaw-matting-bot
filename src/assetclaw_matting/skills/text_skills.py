from __future__ import annotations

from typing import Any

from assetclaw_matting.skills.translation_skills import _complete_deepseek_text, _complete_text


def process_text(
    text: str,
    instruction: str,
    style: str = "natural",
) -> dict[str, Any]:
    cleaned_text = str(text or "").strip()
    cleaned_instruction = str(instruction or "").strip()
    if not cleaned_text:
        raise ValueError("text is required")
    if not cleaned_instruction:
        raise ValueError("instruction is required")
    prompt = (
        "You are a capable text assistant. Complete the user's text-only request directly. "
        "This is a pure text task, so use reasoning freely: translate, polish, rewrite, summarize, organize, "
        "extract action items, or format the content as requested. Return only the final answer. "
        "Do not mention tools, APIs, or implementation details.\n"
        f"Style: {style or 'natural'}\n"
        f"Instruction: {cleaned_instruction}\n\n"
        f"Text:\n{cleaned_text}"
    )
    provider = "deepseek"
    try:
        result = _complete_deepseek_text(prompt).strip()
    except Exception:
        provider = "llm_proxy"
        result = _complete_text(prompt).strip()
    return {
        "ok": True,
        "text": cleaned_text,
        "instruction": cleaned_instruction,
        "result": result,
        "display_text": result,
        "provider": provider,
    }

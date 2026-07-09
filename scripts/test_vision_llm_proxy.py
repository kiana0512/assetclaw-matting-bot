from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from assetclaw_matting.config import settings
from assetclaw_matting.skills.translation_skills import image_describe


def main() -> int:
    print("Vision config:")
    print("  LLM_PROXY_ENABLED =", settings.llm_proxy_enabled)
    print("  LLM_PROXY_BASE_URL set =", bool(settings.llm_proxy_base_url))
    print("  LLM_PROXY_API_KEY set =", bool(settings.llm_proxy_api_key))
    print("  LLM_PROXY_OPENAI_COMPATIBLE =", settings.llm_proxy_openai_compatible)
    print("  LLM_PROXY_MODEL =", settings.llm_proxy_model)
    print("  LLM_PROXY_COMPLEX_MODEL =", settings.llm_proxy_complex_model)

    path = Path("storage/debug/vision_proxy_test.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (320, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 45), "VISION TEST 123", fill="black")
    img.save(path)

    result = image_describe(str(path), "请识别图片里是否有文字，并简短回答。")
    print("")
    print("Result:")
    print(result.get("text", ""))
    print("VISION_AVAILABLE =", bool(result.get("vision_available", True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

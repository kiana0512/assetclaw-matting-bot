from __future__ import annotations

from pathlib import Path

from PIL import Image

from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.brain.multimodal_planner import answer_recent_image_question
from assetclaw_matting.brain.text_planner import plan_text_task
from assetclaw_matting.brain.translation_planner import plan_translation_task
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.translation_skills import _image_mime, image_describe, image_ocr, translate_image_text, translate_text
from assetclaw_matting.skills.text_skills import process_text


def test_plan_text_translation() -> None:
    planned = plan_translation_task(BrainMessage(text="把这句话翻译成英文：今天辛苦了"))

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "translate.text"
    assert tool_calls[0].arguments["target_language"] == "English"
    assert tool_calls[0].arguments["text"] == "今天辛苦了"


def test_plan_image_translation() -> None:
    path = Path.cwd() / "storage/debug/translate_image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)

    planned = plan_translation_task(
        BrainMessage(
            text="把这个图里的文字翻译成中文",
            attachments=[{"type": "image", "local_path": str(path)}],
        )
    )

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "translate.image_text"
    assert tool_calls[0].arguments["target_language"] == "Chinese"
    assert tool_calls[0].arguments["image_path"] == str(path)


def test_image_mime_uses_file_signature_not_extension() -> None:
    path = Path.cwd() / "storage/debug/jpeg_named_png.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (255, 255, 255)).save(path, format="JPEG")

    assert _image_mime(path) == "image/jpeg"


def test_plan_mixed_image_translation_text() -> None:
    path = Path.cwd() / "storage/debug/mixed_translate_image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)

    planned = plan_translation_task(
        BrainMessage(
            text="提取并且翻译这个图片中的文字为日语ok",
            attachments=[{"type": "image", "local_path": str(path)}],
        )
    )

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "translate.image_text"
    assert tool_calls[0].arguments["target_language"] == "Japanese"


def test_plan_image_placeholder_without_download_does_not_translate_instruction() -> None:
    planned = plan_translation_task(BrainMessage(text="[图片]提取并且翻译这个图片中的文字为日语ok"))

    assert planned is not None
    tool_calls, text = planned
    assert tool_calls == []
    assert "没拿到" in text


def test_plan_image_download_permission_error_is_clear() -> None:
    planned = plan_translation_task(
        BrainMessage(
            text="提取图片中的文字",
            attachments=[{
                "type": "image",
                "downloaded": False,
                "error": "download_message_resource failed: 400 {\"code\":99991672,\"msg\":\"Access denied\"}",
            }],
        )
    )

    assert planned is not None
    tool_calls, text = planned
    assert tool_calls == []
    assert "缺少消息资源读取权限" in text


def test_plan_image_reference_without_download_asks_for_image() -> None:
    planned = plan_translation_task(BrainMessage(text="提取图片中的文字"))

    assert planned is not None
    tool_calls, text = planned
    assert tool_calls == []
    assert "图片" in text


def test_plan_image_ocr_from_attachment() -> None:
    path = Path.cwd() / "storage/debug/ocr_image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)

    planned = plan_translation_task(
        BrainMessage(
            text="提取文字",
            attachments=[{"type": "image", "local_path": str(path)}],
        )
    )

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "image.ocr"
    assert tool_calls[0].arguments["image_path"] == str(path)


def test_plan_image_only_attachment_continues_recent_ocr_request() -> None:
    from assetclaw_matting.db.repos import insert_brain_message
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    conversation_id = "image-only-after-ocr-request"
    insert_brain_message(
        provider="test",
        channel="feishu",
        conversation_id=conversation_id,
        user_id="user",
        message_text="提取图片中的文字",
        response_text="可以，把图片发我。",
        tool_calls_json="[]",
        raw_json="{}",
    )
    path = Path.cwd() / "storage/debug/image_only_ocr.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)

    planned = plan_translation_task(
        BrainMessage(
            conversation_id=conversation_id,
            text="",
            attachments=[{"type": "image", "local_path": str(path)}],
        )
    )

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "image.ocr"
    assert tool_calls[0].arguments["image_path"] == str(path)


def test_plan_image_ocr_from_recent_memory() -> None:
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    path = Path.cwd() / "storage/debug/recent_ocr_image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)
    upsert_memory_note("translation-recent-image", "last_image_path", str(path), source="test")

    planned = plan_translation_task(BrainMessage(conversation_id="translation-recent-image", text="识别刚刚那张图里的文字"))

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "image.ocr"
    assert tool_calls[0].arguments["image_path"] == str(path)


def test_recent_image_does_not_hijack_plain_text_translation() -> None:
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    path = Path.cwd() / "storage/debug/recent_plain_translate.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)
    upsert_memory_note("plain-text-translation", "last_image_path", str(path), source="test")

    planned = plan_translation_task(BrainMessage(conversation_id="plain-text-translation", text="可以翻译下面的一句话为日语吗 早上好"))

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "translate.text"
    assert tool_calls[0].arguments["text"] == "早上好"


def test_answer_recent_image_question() -> None:
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    path = Path.cwd() / "storage/debug/recent_answer_image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)
    upsert_memory_note("recent-image-answer", "last_image_path", str(path), source="test")

    text = answer_recent_image_question(BrainMessage(conversation_id="recent-image-answer", text="你收到了我的图片吗"))

    assert text is not None
    assert path.name in text


def test_answer_previous_image_question() -> None:
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()
    path = Path.cwd() / "storage/debug/previous_answer_image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)
    upsert_memory_note("previous-image-answer", "last_image_path", str(path), source="test")

    text = answer_recent_image_question(BrainMessage(conversation_id="previous-image-answer", text="还记得我之前发的图片吗"))

    assert text is not None
    assert "记得" in text
    assert path.name in text


def test_translation_formatter_is_plain_text() -> None:
    text = format_skill_results([{
        "ok": True,
        "skill": "translate.text",
        "result": {"translation": "Thanks for your hard work today."},
    }])

    assert text == "Thanks for your hard work today."


def test_translate_text_uses_deepseek_when_llm_proxy_is_unconfigured(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    import assetclaw_matting.skills.translation_skills as translation_skills

    monkeypatch.setattr(settings, "llm_proxy_enabled", False)
    prompts: list[str] = []

    def fake_deepseek(prompt: str) -> str:
        prompts.append(prompt)
        return "I like you, Miku."

    monkeypatch.setattr(translation_skills, "_complete_deepseek_text", fake_deepseek)

    result = translate_text("我喜欢你初音", target_language="English")

    assert result["ok"] is True
    assert result["translation"] == "I like you, Miku."
    assert result["display_text"] == "I like you, Miku."
    assert "Target language: English" in prompts[0]


def test_process_text_uses_deepseek_for_plain_text_tasks(monkeypatch) -> None:
    import assetclaw_matting.skills.text_skills as text_skills

    prompts: list[str] = []

    def fake_deepseek(prompt: str) -> str:
        prompts.append(prompt)
        return "今天辛苦了，感谢大家的推进。"

    monkeypatch.setattr(text_skills, "_complete_deepseek_text", fake_deepseek)

    result = process_text("今天大家搞得挺累但有进度", "润色这段话")

    assert result["ok"] is True
    assert result["provider"] == "deepseek"
    assert result["display_text"] == "今天辛苦了，感谢大家的推进。"
    assert "pure text task" in prompts[0]


def test_plan_text_task_routes_explicit_text_only() -> None:
    planned = plan_text_task(BrainMessage(text="润色这段话：今天大家搞得挺累但有进度"))

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "text.process"
    assert tool_calls[0].arguments["text"] == "今天大家搞得挺累但有进度"
    assert "润色" in tool_calls[0].arguments["instruction"]

    assert plan_text_task(BrainMessage(text="整理 .\\docs 里的文件")) is None


def test_ocr_formatter_is_plain_text() -> None:
    text = format_skill_results([{
        "ok": True,
        "skill": "image.ocr",
        "result": {"text": "Hello"},
    }])

    assert text == "Hello"


def test_image_ocr_falls_back_to_local_ocr_when_llm_proxy_is_unconfigured(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    import assetclaw_matting.skills.translation_skills as translation_skills

    monkeypatch.setattr(settings, "llm_proxy_enabled", False)
    monkeypatch.setattr(
        translation_skills,
        "local_ocr_image",
        lambda path: {"available": True, "engine": "fake_local_ocr", "text": "HELLO 123"},
        raising=False,
    )
    path = Path.cwd() / "storage/debug/ocr_unconfigured.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 6), (255, 255, 255, 255)).save(path)

    result = image_ocr(str(path))

    assert result["ok"] is True
    assert result["local_ocr"] is True
    assert result["text"] == "HELLO 123"


def test_translate_image_text_falls_back_to_local_ocr_and_text_translation(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    import assetclaw_matting.skills.translation_skills as translation_skills

    monkeypatch.setattr(settings, "llm_proxy_enabled", False)
    monkeypatch.setattr(
        translation_skills,
        "local_ocr_image",
        lambda path: {"available": True, "engine": "fake_local_ocr", "text": "我没事儿"},
        raising=False,
    )
    prompts: list[str] = []

    def fake_deepseek(prompt: str) -> str:
        prompts.append(prompt)
        return "This screenshot lists several desktop automation skills, including browser control, file management, screenshots, OCR, and office automation."

    monkeypatch.setattr(translation_skills, "_complete_deepseek_text", fake_deepseek)
    path = Path.cwd() / "storage/debug/translate_ocr_fallback.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 6), (255, 255, 255, 255)).save(path)

    result = translate_image_text(str(path), target_language="English", instruction="提取文字并翻译成英语")

    assert result["ok"] is True
    assert result["local_ocr"] is True
    assert result["fallback"] == "local_ocr_text_translation"
    assert result["text"] == "我没事儿"
    assert result["translation"].startswith("This screenshot lists")
    assert result["display_text"] == result["translation"]
    assert "Do not dump raw OCR" in prompts[0]
    assert "remove obvious OCR noise" in prompts[0]


def test_translation_formatter_prefers_display_text_for_image_translation() -> None:
    text = format_skill_results([{
        "ok": True,
        "skill": "translate.image_text",
        "result": {
            "text": "Old OpenClaw Bloated Day qqEN}t °C 2C357...",
            "translation": "Old OpenClaw Bloated Day qqEN}t °C 2C357...",
            "display_text": "This screenshot is a rough skills list for a desktop automation agent.",
        },
    }])

    assert text == "This screenshot is a rough skills list for a desktop automation agent."
    assert "qqEN" not in text


def test_image_describe_fallback_runs_local_ocr(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    import assetclaw_matting.skills.translation_skills as translation_skills

    monkeypatch.setattr(settings, "llm_proxy_enabled", False)
    monkeypatch.setattr(
        translation_skills,
        "local_ocr_image",
        lambda path: {"available": True, "engine": "fake_local_ocr", "text": "WOW"},
    )
    monkeypatch.setattr(translation_skills, "_complete_deepseek_text", lambda prompt: "这是一个带 WOW 文字的表情包，情绪偏惊喜。")
    path = Path.cwd() / "storage/debug/describe_unconfigured.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 6), (255, 255, 255, 255)).save(path)

    result = image_describe(str(path), "分析这张表情包")

    assert result["ok"] is True
    assert result["local_ocr"] is True
    assert result["analysis_engine"] == "deepseek"
    assert result["ocr_text"] == "WOW"
    assert "表情包" in result["text"]
    assert "WOW" in result["text"]


def test_image_describe_fallback_reports_empty_local_ocr(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    import assetclaw_matting.skills.translation_skills as translation_skills

    monkeypatch.setattr(settings, "llm_proxy_enabled", False)
    monkeypatch.setattr(
        translation_skills,
        "local_ocr_image",
        lambda path: {"available": True, "engine": "fake_local_ocr", "text": ""},
    )
    path = Path.cwd() / "storage/debug/describe_empty_ocr.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 6), (255, 255, 255, 255)).save(path)

    result = image_describe(str(path), "分析这张表情包")

    assert result["ok"] is True
    assert result["local_ocr"] is True
    assert "没有识别到清晰文字" in result["text"]


def test_zip_and_send_formatter_reports_send() -> None:
    text = format_skill_results([{
        "ok": True,
        "skill": "feishu.zip_and_send",
        "result": {"file_name": "input_backup.zip", "count": 162},
    }])

    assert text == "已打包并发送：input_backup.zip（162 个文件）"


def test_llm_proxy_brain_does_not_send_empty_user_message(monkeypatch) -> None:
    from assetclaw_matting.brain.llm_proxy_brain import LLMProxyBrain
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "llm_proxy_enabled", True)
    monkeypatch.setattr(settings, "llm_proxy_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "llm_proxy_api_key", "test-key")

    def fail_complete(*_: object, **__: object) -> str:
        raise AssertionError("empty message should not call LLM Proxy")

    brain = LLMProxyBrain()
    monkeypatch.setattr(brain, "_complete", fail_complete)

    response = brain.handle_message(BrainMessage(text=""))

    assert "空消息" in response.text

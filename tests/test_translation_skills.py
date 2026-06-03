from __future__ import annotations

from pathlib import Path

from PIL import Image

from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.brain.multimodal_planner import answer_recent_image_question
from assetclaw_matting.brain.translation_planner import plan_translation_task
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.translation_skills import _image_mime


def test_plan_text_translation() -> None:
    planned = plan_translation_task(BrainMessage(text="把这句话翻译成英文：今天辛苦了"))

    assert planned is not None
    tool_calls, _ = planned
    assert tool_calls[0].skill == "translate.text"
    assert tool_calls[0].arguments["target_language"] == "English"
    assert tool_calls[0].arguments["text"] == "今天辛苦了"


def test_plan_image_translation() -> None:
    path = Path("E:/assetclaw-matting-bot/storage/debug/translate_image.png")
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
    path = Path("E:/assetclaw-matting-bot/storage/debug/jpeg_named_png.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (255, 255, 255)).save(path, format="JPEG")

    assert _image_mime(path) == "image/jpeg"


def test_plan_mixed_image_translation_text() -> None:
    path = Path("E:/assetclaw-matting-bot/storage/debug/mixed_translate_image.png")
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
    path = Path("E:/assetclaw-matting-bot/storage/debug/ocr_image.png")
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

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
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
    path = Path("E:/assetclaw-matting-bot/storage/debug/image_only_ocr.png")
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

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()
    path = Path("E:/assetclaw-matting-bot/storage/debug/recent_ocr_image.png")
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

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()
    path = Path("E:/assetclaw-matting-bot/storage/debug/recent_plain_translate.png")
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

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()
    path = Path("E:/assetclaw-matting-bot/storage/debug/recent_answer_image.png")
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

    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()
    path = Path("E:/assetclaw-matting-bot/storage/debug/previous_answer_image.png")
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


def test_ocr_formatter_is_plain_text() -> None:
    text = format_skill_results([{
        "ok": True,
        "skill": "image.ocr",
        "result": {"text": "Hello"},
    }])

    assert text == "Hello"


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

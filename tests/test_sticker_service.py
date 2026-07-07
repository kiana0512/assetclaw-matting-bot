from __future__ import annotations

from pathlib import Path

from PIL import Image

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
from assetclaw_matting.services import sticker_service
from assetclaw_matting.skills.registry import call_skill, get_skill_meta


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_sticker_status_and_choose(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"png")
    (tmp_path / "b.gif").write_bytes(b"gif")
    (tmp_path / "manifest.jsonl").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(settings, "bot_sticker_dir", tmp_path)
    monkeypatch.setattr(settings, "bot_sticker_extensions", ".png;.gif")
    monkeypatch.setattr(settings, "bot_sticker_max_bytes", 100)
    monkeypatch.setattr(settings, "bot_sticker_probability", 1.0)
    monkeypatch.setattr(settings, "bot_emotional_replies_enabled", True)

    status = sticker_service.sticker_status()
    assert status["count"] == 2
    assert sticker_service.choose_sticker(reply_text="完成了") is not None
    assert sticker_service.choose_sticker(reply_text="收到，处理中。") is None


def test_sticker_registry_router_and_formatter(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"png")
    monkeypatch.setattr(settings, "bot_sticker_dir", tmp_path)
    monkeypatch.setattr(settings, "bot_sticker_extensions", ".png")

    assert get_skill_meta("sticker.info")
    assert get_skill_meta("sticker.send_random")
    result = call_skill("sticker.info", {}, requested_by="test")
    assert result["ok"] is True
    text = format_skill_results([result])
    assert "情绪表情回复" in text
    assert "可用表情：1 个" in text

    brain = LocalCommandBrain()
    response = brain.handle_message(BrainMessage(text="表情包状态"))
    assert response.tool_calls
    assert response.tool_calls[0].skill == "sticker.info"


def test_sticker_send_random_uses_feishu_context(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "a.png"
    path.write_bytes(b"png")
    sent: list[tuple[str, Path]] = []

    monkeypatch.setattr(settings, "bot_sticker_dir", tmp_path)
    monkeypatch.setattr(settings, "bot_sticker_extensions", ".png")

    from assetclaw_matting.feishu.client import feishu_client

    monkeypatch.setattr(feishu_client, "send_image_to_chat", lambda chat_id, target: sent.append((chat_id, target)))
    token = set_runtime_context(channel="feishu", chat_id="chat-test", conversation_id="conv")
    try:
        result = call_skill("sticker.send_random", {}, requested_by="test")
    finally:
        reset_runtime_context(token)

    assert result["ok"] is True
    assert result["result"]["sent"] is True
    assert sent == [("chat-test", path)]


def test_sticker_send_random_resizes_large_png(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "large.png"
    Image.new("RGBA", (900, 600), (0, 255, 255, 255)).save(path)
    sent: list[tuple[str, Path]] = []

    monkeypatch.setattr(settings, "bot_sticker_dir", tmp_path)
    monkeypatch.setattr(settings, "bot_sticker_extensions", ".png")
    monkeypatch.setattr(settings, "bot_sticker_send_max_px", 160)
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")

    from assetclaw_matting.feishu.client import feishu_client

    monkeypatch.setattr(feishu_client, "send_image_to_chat", lambda chat_id, target: sent.append((chat_id, target)))
    token = set_runtime_context(channel="feishu", chat_id="chat-test", conversation_id="conv")
    try:
        result = call_skill("sticker.send_random", {}, requested_by="test")
    finally:
        reset_runtime_context(token)

    assert result["ok"] is True
    assert sent and sent[0][1] != path
    with Image.open(sent[0][1]) as img:
        assert max(img.size) == 160
        assert img.size == (160, 107)


def test_sticker_send_random_resizes_large_gif_proportionally(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "large.gif"
    frames = [
        Image.new("RGBA", (600, 300), (255, 0, 0, 255)),
        Image.new("RGBA", (600, 300), (0, 255, 0, 255)),
    ]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[80, 90], loop=0)
    sent: list[tuple[str, Path]] = []

    monkeypatch.setattr(settings, "bot_sticker_dir", tmp_path)
    monkeypatch.setattr(settings, "bot_sticker_extensions", ".gif")
    monkeypatch.setattr(settings, "bot_sticker_send_max_px", 120)
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")

    from assetclaw_matting.feishu.client import feishu_client

    monkeypatch.setattr(feishu_client, "send_image_to_chat", lambda chat_id, target: sent.append((chat_id, target)))
    token = set_runtime_context(channel="feishu", chat_id="chat-test", conversation_id="conv")
    try:
        result = call_skill("sticker.send_random", {}, requested_by="test")
    finally:
        reset_runtime_context(token)

    assert result["ok"] is True
    assert sent and sent[0][1] != path
    assert sent[0][1].suffix == ".gif"
    with Image.open(sent[0][1]) as img:
        assert max(img.size) == 120
        assert img.size == (120, 60)
        assert getattr(img, "is_animated", False) is True

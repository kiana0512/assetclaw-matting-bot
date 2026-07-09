from __future__ import annotations

from pathlib import Path

from assetclaw_matting.brain.context_builder import build_memory_prompt
from assetclaw_matting.db.repos import count_brain_messages, get_conversation_summary, insert_brain_message
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_brain_messages_auto_compact(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    conversation_id = "compact-test"
    monkeypatch.setattr(settings, "brain_memory_compact_enabled", True)
    monkeypatch.setattr(settings, "brain_memory_compact_after_messages", 5)
    monkeypatch.setattr(settings, "brain_memory_compact_keep_messages", 2)
    monkeypatch.setattr(settings, "brain_memory_compact_max_chars", 800)

    for index in range(7):
        insert_brain_message(
            provider="test",
            channel="feishu",
            conversation_id=conversation_id,
            user_id="user",
            message_text=f"用户旧消息 {index}",
            response_text=f"助手旧回复 {index}",
            tool_calls_json="[]",
            raw_json="{}",
        )

    assert count_brain_messages(conversation_id) <= 5
    summary = get_conversation_summary(conversation_id)
    assert summary is not None
    assert "用户旧消息 0" in summary["summary_text"]
    assert "助手旧回复 0" in summary["summary_text"]

    prompt = build_memory_prompt(conversation_id)
    assert "Compacted earlier conversation" in prompt
    assert "Recent conversation" in prompt


def test_compaction_sends_short_feishu_hint(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.feishu.client import feishu_client
    from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context

    conversation_id = "compact-notify-test"
    sent: list[str] = []
    monkeypatch.setattr(settings, "brain_memory_compact_enabled", True)
    monkeypatch.setattr(settings, "brain_memory_compact_notify_feishu", True)
    monkeypatch.setattr(settings, "brain_memory_compact_after_messages", 3)
    monkeypatch.setattr(settings, "brain_memory_compact_keep_messages", 2)
    monkeypatch.setattr(feishu_client, "send_text_to_chat", lambda chat_id, text: sent.append(text))

    token = set_runtime_context(channel="feishu", chat_id="chat_test")
    try:
        for index in range(5):
            insert_brain_message(
                provider="test",
                channel="feishu",
                conversation_id=conversation_id,
                user_id="user",
                message_text=f"压缩提示测试 {index}",
                response_text="ok",
                tool_calls_json="[]",
                raw_json="{}",
            )
    finally:
        reset_runtime_context(token)

    assert sent == ["上下文已整理，会继续接着聊。"]

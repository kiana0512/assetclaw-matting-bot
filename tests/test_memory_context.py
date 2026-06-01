from __future__ import annotations

from pathlib import Path

from assetclaw_matting.brain.context_builder import build_memory_prompt
from assetclaw_matting.db.repos import insert_brain_message, upsert_memory_note
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_memory_prompt_includes_recent_conversation_and_notes() -> None:
    insert_brain_message(
        provider="test",
        channel="brain_test",
        conversation_id="memory-test",
        user_id="user",
        message_text="我刚才说项目根目录是 E:\\assetclaw-matting-bot",
        response_text="记住了。",
        tool_calls_json="[]",
        raw_json="{}",
    )
    upsert_memory_note("memory-test", "project_root", "E:\\assetclaw-matting-bot", source="test")
    upsert_memory_note("other-user", "secret_note", "should-not-leak", source="test")
    prompt = build_memory_prompt("memory-test")
    assert "LOCAL MEMORY FROM SQLITE" in prompt
    assert "项目根目录" in prompt
    assert "project_root" in prompt
    assert "secret_note" not in prompt

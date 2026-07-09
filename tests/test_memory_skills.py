from __future__ import annotations

from pathlib import Path

from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.registry import call_skill


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_memory_remember_and_list() -> None:
    saved = call_skill(
        "memory.remember",
        {"key": "today_test_project_dir", "value": "E:\\assetclaw-matting-bot"},
        requested_by="test",
    )
    assert saved["ok"] is True
    listed = call_skill("memory.list", {"scope": "global"}, requested_by="test")
    assert listed["ok"] is True
    assert any(item["key"] == "today_test_project_dir" for item in listed["result"]["items"])

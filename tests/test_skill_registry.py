from __future__ import annotations

from pathlib import Path

from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.registry import call_skill


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def test_known_skill() -> None:
    result = call_skill("file.list_allowed", {"path": "E:\\"}, requested_by="test")
    assert result["ok"] is True


def test_unknown_skill() -> None:
    result = call_skill("nope.skill", {}, requested_by="test")
    assert result["ok"] is False


def test_new_file_skills_registered() -> None:
    for skill in ("file.exists", "file.mkdir"):
        result = call_skill(skill, {"path": "E:\\assetclaw-matting-bot\\storage\\debug"}, requested_by="test")
        assert result["ok"] is True


def test_feishu_send_file_skills_do_not_require_confirmation() -> None:
    from assetclaw_matting.skills.registry import get_skill_meta

    assert get_skill_meta("feishu.send_file")["requires_confirmation"] is False
    assert get_skill_meta("feishu.send_file_by_name")["requires_confirmation"] is False


def test_direct_video_skills_registered_with_confirmation() -> None:
    from assetclaw_matting.skills.registry import get_skill_meta

    assert get_skill_meta("direct_video.start")["requires_confirmation"] is True
    assert get_skill_meta("direct_video.status")["requires_confirmation"] is False
    assert get_skill_meta("direct_video.resend_zip")["requires_confirmation"] is False
    assert get_skill_meta("direct_video.start")["domain"] == "direct_video"

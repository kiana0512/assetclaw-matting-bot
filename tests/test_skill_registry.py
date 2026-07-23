from __future__ import annotations

from pathlib import Path

from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import get_connection, init_db
from assetclaw_matting.db.repos import insert_skill_call
from assetclaw_matting.skills.registry import call_skill


def setup_module() -> None:
    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()


def test_known_skill() -> None:
    result = call_skill("file.list_allowed", {"path": str(Path.cwd())}, requested_by="test")
    assert result["ok"] is True


def test_unknown_skill() -> None:
    result = call_skill("nope.skill", {}, requested_by="test")
    assert result["ok"] is False


def test_successful_webui_read_poll_is_not_persisted() -> None:
    with get_connection() as conn:
        before = conn.execute("SELECT COUNT(*) FROM skill_calls").fetchone()[0]

    result = call_skill("file.list_allowed", {"path": str(Path.cwd())}, requested_by="webui")

    assert result["ok"] is True
    with get_connection() as conn:
        after = conn.execute("SELECT COUNT(*) FROM skill_calls").fetchone()[0]
    assert after == before


def test_large_audit_result_is_compacted_to_valid_json() -> None:
    insert_skill_call(
        "large-audit",
        "test.large",
        {},
        {"ok": True, "skill": "test.large", "result": {"items": ["x" * 1000] * 100}},
        True,
        "",
        "test",
    )

    with get_connection() as conn:
        row = conn.execute(
            "SELECT result_json FROM skill_calls WHERE request_id = 'large-audit'"
        ).fetchone()
    import json

    payload = json.loads(row["result_json"])
    assert payload["audit_truncated"] is True
    assert payload["original_chars"] > 32_000
    assert len(row["result_json"]) < 32_000


def test_new_file_skills_registered() -> None:
    for skill in ("file.exists", "file.mkdir"):
        result = call_skill(skill, {"path": ".\\storage\\debug"}, requested_by="test")
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
    assert get_skill_meta("direct_video.cancel")["requires_confirmation"] is False
    assert get_skill_meta("direct_video.start")["domain"] == "direct_video"


def test_direct_image_skills_registered_without_confirmation() -> None:
    from assetclaw_matting.skills.registry import get_skill_meta

    assert get_skill_meta("direct_image.start")["requires_confirmation"] is False
    assert get_skill_meta("direct_image.status")["requires_confirmation"] is False
    assert get_skill_meta("direct_image.cancel")["requires_confirmation"] is False
    assert get_skill_meta("direct_image.resume_postprocess")["requires_confirmation"] is False
    assert get_skill_meta("direct_image.start")["domain"] == "direct_image"


def test_matting_pipeline_skills_registered() -> None:
    from assetclaw_matting.skills.registry import get_skill_meta

    assert get_skill_meta("matting_pipeline.status")["requires_confirmation"] is False
    assert get_skill_meta("matting_pipeline.verify")["requires_confirmation"] is False
    assert get_skill_meta("matting_pipeline.update")["requires_confirmation"] is False
    assert get_skill_meta("matting_pipeline.update")["domain"] == "matting_pipeline"

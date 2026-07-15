from __future__ import annotations

from pathlib import Path

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.animation_ops_skills import (
    preview_manual_smooth_current_confirmation,
    preview_rerun_from_videos_confirmation,
    status,
)
from assetclaw_matting.skills.registry import get_skill_meta


def _workspace() -> Path:
    root = Path.cwd() / "storage/debug/test_animation_ops"
    for dirname in ("videos/gary/idle", "frames/gary/idle", "matte/gary/idle", "smooth/gary/idle"):
        (root / dirname).mkdir(parents=True, exist_ok=True)
    (root / "videos/gary/idle/source.mp4").write_bytes(b"video")
    (root / "frames/gary/idle/0001.png").write_bytes(b"frame")
    (root / "frames/gary/idle/0002.png").write_bytes(b"frame")
    (root / "matte/gary/idle/0001.png").write_bytes(b"matte")
    return root


def test_animation_status_counts_workspace_and_formats_result() -> None:
    root = _workspace()

    payload = status(str(root), include_runs=False)

    assert payload["counts"]["videos"] == 1
    assert payload["counts"]["frames"] == 2
    assert payload["counts"]["matte"] == 1
    assert payload["matte_matches_frames"] is False

    text = format_skill_results([{"ok": True, "skill": "animation.status", "result": payload}])
    assert "动画工作区状态" in text
    assert "frames 2" in text
    assert "matte 对齐 frames：否" in text


def test_animation_ops_registry_requires_confirmation_for_writes() -> None:
    assert get_skill_meta("animation.status")["requires_confirmation"] is False
    assert get_skill_meta("animation.manual_smooth_current")["requires_confirmation"] is True
    assert get_skill_meta("animation.rerun_from_videos")["requires_confirmation"] is True


def test_animation_confirmation_previews_are_specific() -> None:
    root = _workspace()

    smooth_preview = preview_manual_smooth_current_confirmation({"root": str(root)}, "abc123")
    rerun_preview = preview_rerun_from_videos_confirmation({"root": str(root), "fps": 24}, "def456")

    assert "基于当前 matte" in smooth_preview
    assert "确认执行 abc123" in smooth_preview
    assert "归档并重建" in rerun_preview
    assert "确认执行 def456" in rerun_preview


def test_local_brain_routes_animation_production_requests() -> None:
    brain = LocalCommandBrain()

    status_call = brain._infer_tool_calls(r"看一下 E:\animation_automation\2026-06-02 当前有多少帧")
    rerun_call = brain._infer_tool_calls(r"动画流程全部重做 输入为 E:\animation_automation\2026-06-02")
    smooth_call = brain._infer_tool_calls(
        r"帮我手动做一下平滑 输入为 E:\animation_automation\2026-06-02\matte 输出为 E:\animation_automation\2026-06-02\smooth"
    )

    assert status_call[0].skill == "animation.status"
    assert rerun_call[0].skill == "animation.rerun_from_videos"
    assert smooth_call[0].skill == "animation.manual_smooth_current"
    assert smooth_call[0].arguments["input_dir"].endswith(r"\matte")
    assert smooth_call[0].arguments["output_dir"].endswith(r"\smooth")

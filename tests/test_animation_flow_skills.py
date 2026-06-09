from __future__ import annotations

from pathlib import Path

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.animation_flow_skills import run_preview
from assetclaw_matting.skills.registry import get_skill_meta
from assetclaw_matting.skills.unity_import_skills import preview as unity_preview, run_import


def _unity_ready_fixture() -> Path:
    root = Path("E:/assetclaw-matting-bot/storage/debug/test_unity_ready")
    scene = root / "scene"
    frames = scene / "frames" / "heather-idle"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "0000.png").write_bytes(b"png")
    (scene / "animation_resource_manifest.json").write_text(
        '{"items":{"heather":{"idle":{"name":"heather idle","types":["角色动画"]}}}}',
        encoding="utf-8",
    )
    emoji = root / "emoji"
    (emoji / "frames").mkdir(parents=True, exist_ok=True)
    (emoji / "animation_resource_manifest.json").write_text('{"items":{}}', encoding="utf-8")
    return root


def test_animation_flow_registry_and_router() -> None:
    assert get_skill_meta("animation_flow.start")["requires_confirmation"] is True
    assert get_skill_meta("unity_import.run")["requires_confirmation"] is True

    call = LocalCommandBrain()._infer_tool_calls("开始这个动画自动化流程")

    assert call[0].skill == "animation_flow.start"


def test_animation_flow_preview_formats_seven_steps() -> None:
    payload = run_preview(date_root="E:/animation_automation/2026-06-09")
    text = format_skill_results([{"ok": True, "skill": "animation_flow.preview", "result": payload}])

    assert len(payload["stages"]) == 7
    assert "Unity 插件导入引擎" in text
    assert "P4 submit：disabled" in text


def test_unity_import_preview_reads_unity_ready_and_refuses_when_mcp_off() -> None:
    ready = _unity_ready_fixture()

    payload = unity_preview(str(ready), unity_project="D:/Spark/Client", package="scene", mcp_url="http://127.0.0.1:1/mcp")
    result = run_import(str(ready), unity_project="D:/Spark/Client", package="scene", mcp_url="http://127.0.0.1:1/mcp")

    assert payload["packages"][0]["task_count"] == 1
    assert payload["packages"][0]["tasks"][0]["source_dir"].endswith("heather-idle")
    assert result["ok"] is False
    assert result["error"] == "unity_mcp_off"

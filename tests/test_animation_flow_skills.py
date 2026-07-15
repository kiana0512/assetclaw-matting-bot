from __future__ import annotations

import shutil
import json
from pathlib import Path

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.config import settings
from assetclaw_matting.skills import animation_flow_skills
from assetclaw_matting.skills.animation_flow_skills import run_preview
from assetclaw_matting.skills.pipeline_skills import _route_cherry_options
from assetclaw_matting.skills.registry import get_skill_meta
from assetclaw_matting.skills.unity_import_skills import preview as unity_preview, run_import


def _unity_ready_fixture() -> Path:
    root = Path.cwd() / "storage/debug/test_unity_ready"
    shutil.rmtree(root, ignore_errors=True)
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
    assert get_skill_meta("animation_flow.resume")["requires_confirmation"] is False
    assert get_skill_meta("unity_import.run")["requires_confirmation"] is True

    call = LocalCommandBrain()._infer_tool_calls("开始这个动画自动化流程")

    assert call[0].skill == "animation_flow.start"

    iteration = LocalCommandBrain()._infer_tool_calls("开始这个动画自动化流程 迭代")
    assert iteration[0].skill == "animation_flow.start"
    assert iteration[0].arguments["unity_import_mode"] == "iteration"

    compact = LocalCommandBrain()._infer_tool_calls("动画自动化20260610 迭代")
    assert compact[0].skill == "animation_flow.start"
    assert Path(compact[0].arguments["date_root"]) == settings.animation_root / "2026-06-10"
    assert compact[0].arguments["unity_import_mode"] == "iteration"

    priority = LocalCommandBrain()._infer_tool_calls("动画自动化20260612 替换 优先 casualheather")
    assert priority[0].skill == "animation_flow.start"
    assert Path(priority[0].arguments["date_root"]) == settings.animation_root / "2026-06-12"
    assert priority[0].arguments["unity_import_mode"] == "iteration"
    assert priority[0].arguments["priority_characters"] == ["casualheather"]

    resume = LocalCommandBrain()._infer_tool_calls("继续完整动画流程 AFLOW_5080CB9A1E3B")
    assert resume[0].skill == "animation_flow.resume"
    assert resume[0].arguments["run_id"] == "AFLOW_5080CB9A1E3B"

    single = LocalCommandBrain()._infer_tool_calls(r"Unity 插件对 E:\animation_automation\2026-06-09\unity_ready 做资源迭代")
    assert single[0].skill == "unity_import.run"
    assert single[0].arguments["mode"] == "iteration"


def test_animation_flow_preview_formats_seven_steps() -> None:
    workflow = Path.cwd() / "storage" / "debug" / "animation_preview_workflow.json"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text('{"1":{"class_type":"LoadImage","inputs":{"image":""}}}', encoding="utf-8")
    payload = run_preview(
        date_root=str(settings.animation_root / "2026-06-09"),
        unity_import_mode="iteration",
        workflow_path=str(workflow),
    )
    text = format_skill_results([{"ok": True, "skill": "animation_flow.preview", "result": payload}])

    assert len(payload["stages"]) == 7
    assert payload["unity_import_mode"] == "iteration"
    assert payload["feishu_progress_policy"]["include"] == ["待抽帧"]
    assert "Cherry 平滑后处理" in " ".join(stage["label"] for stage in payload["stages"])
    assert "Unity 插件导入引擎" in text
    assert "仅处理 待抽帧" in text
    assert "P4 submit：disabled" in text


def test_animation_flow_cherry_route_presets() -> None:
    scene = _route_cherry_options(Path("E:/animation_automation/2026-06-17/scene"))
    emoji = _route_cherry_options(Path("E:/animation_automation/2026-06-17/emoji"))
    story = _route_cherry_options(Path("E:/animation_automation/2026-06-17/story"))
    temporal = _route_cherry_options(Path("E:/animation_automation/2026-06-17/scene/temporal_smooth"))

    assert scene is not None
    assert scene["resize_width"] == 384
    assert scene["resize_height"] == 512
    assert scene["use_shadow"] is True
    assert scene["use_resize2"] is True
    assert scene["use_smooth"] is False

    assert emoji is not None
    assert emoji["resize_width"] == 256
    assert emoji["resize_height"] == 256
    assert emoji["use_shadow"] is False
    assert emoji["use_resize2"] is True
    assert emoji["use_smooth"] is False

    assert story is not None
    assert story["resize_width"] == 256
    assert story["resize_height"] == 256
    assert story["use_shadow"] is False
    assert story["use_resize2"] is True
    assert story["use_smooth"] is False

    assert temporal is not None
    assert temporal["use_smooth"] is True


def test_animation_flow_locks_configured_comfyui_workflow(monkeypatch) -> None:
    workflow = Path.cwd() / "storage/debug/current_animation_workflow.json"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text('{"1":{"class_type":"LoadImage","inputs":{"image":""}}}', encoding="utf-8")

    monkeypatch.setattr(settings, "comfyui_workflow_path", workflow)
    monkeypatch.setattr(animation_flow_skills, "_start_worker", lambda _run_id: None)

    date_root = str(settings.animation_root / "2026-06-11")
    preview = animation_flow_skills.run_preview(date_root=date_root)
    started = animation_flow_skills.run_start(date_root=date_root)

    assert preview["workflow_path"] == str(workflow)
    assert started["workflow_path"] == str(workflow)
    assert started["workflow_name"] == workflow.name


def test_unity_import_preview_reads_unity_ready_and_refuses_when_mcp_off() -> None:
    ready = _unity_ready_fixture()
    project = ready.parent / "UnityProject"
    project.mkdir(parents=True, exist_ok=True)

    payload = unity_preview(str(ready), unity_project=str(project), package="scene", mode="iteration", mcp_url="http://127.0.0.1:1/mcp")
    result = run_import(str(ready), unity_project=str(project), package="scene", mode="iteration", mcp_url="http://127.0.0.1:1/mcp")

    assert payload["mode"] == "iteration"
    assert payload["packages"][0]["task_count"] == 1
    assert payload["packages"][0]["tasks"][0]["source_dir"].endswith("heather-idle")
    assert result["ok"] is False
    assert result["error"] == "unity_mcp_off"


def test_unity_import_preview_skips_empty_package_without_frames_root() -> None:
    ready = _unity_ready_fixture()
    project = ready.parent / "UnityProject"
    project.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(ready / "emoji" / "frames", ignore_errors=True)

    payload = unity_preview(str(ready), unity_project=str(project), package="both", mode="iteration", mcp_url="http://127.0.0.1:1/mcp")

    packages = {item["package"]: item for item in payload["packages"]}
    assert packages["scene"]["task_count"] == 1
    assert packages["emoji"]["task_count"] == 0
    assert packages["emoji"]["frames_root_exists"] is False
    assert packages["emoji"]["skipped"] is True
    assert packages["emoji"]["skip_reason"] == "empty manifest"
    assert packages["story"]["task_count"] == 0
    assert packages["story"]["skipped"] is True


def test_unity_import_timeout_keeps_runner_for_late_unity_completion(monkeypatch) -> None:
    root = Path.cwd() / "storage/debug/test_unity_import_timeout"
    shutil.rmtree(root, ignore_errors=True)
    ready = root / "unity_ready"
    project = root / "UnityProject"
    frames = ready / "scene" / "frames" / "heather-idle"
    frames.mkdir(parents=True)
    (ready / "emoji").mkdir(parents=True)
    (project / "Assets" / "Editor").mkdir(parents=True)
    (project / "Temp").mkdir(parents=True)
    (frames / "0000.png").write_bytes(b"png")
    (ready / "scene" / "animation_resource_manifest.json").write_text(
        '{"items":{"heather":{"idle":{"name":"heather idle","types":["角色动画"]}}}}',
        encoding="utf-8",
    )
    (ready / "emoji" / "animation_resource_manifest.json").write_text('{"items":{}}', encoding="utf-8")

    monkeypatch.setattr("assetclaw_matting.skills.unity_import_skills._probe_mcp", lambda _url: {"available": True})

    result = run_import(str(ready), unity_project=str(project), package="both", mode="iteration", timeout_seconds=1)

    assert result["ok"] is False
    assert result["error"] == "unity_runner_timeout"
    assert (project / "Assets" / "Editor" / "CodexAnimImportApiRunner.cs").exists()
    request_path = Path(result["request"])
    assert request_path.exists()
    assert json.loads(request_path.read_text(encoding="utf-8"))["packages"] == ["scene"]
    assert (project / "Temp" / "CodexAnimImportApiRequest.json").exists()


def test_unity_import_skips_runner_when_all_packages_are_empty(monkeypatch) -> None:
    root = Path.cwd() / "storage/debug/test_unity_import_all_empty"
    shutil.rmtree(root, ignore_errors=True)
    ready = root / "unity_ready"
    project = root / "UnityProject"
    (ready / "scene").mkdir(parents=True)
    (ready / "emoji").mkdir(parents=True)
    (project / "Assets" / "Editor").mkdir(parents=True)
    (ready / "scene" / "animation_resource_manifest.json").write_text('{"items":{}}', encoding="utf-8")
    (ready / "emoji" / "animation_resource_manifest.json").write_text('{"items":{}}', encoding="utf-8")

    monkeypatch.setattr("assetclaw_matting.skills.unity_import_skills._probe_mcp", lambda _url: {"available": True})

    result = run_import(str(ready), unity_project=str(project), package="both", mode="iteration", timeout_seconds=1)

    assert result["ok"] is True
    assert result["result"]["message"] == "No Unity import packages have tasks; skipped Unity runner."
    assert not (project / "Assets" / "Editor" / "CodexAnimImportApiRunner.cs").exists()

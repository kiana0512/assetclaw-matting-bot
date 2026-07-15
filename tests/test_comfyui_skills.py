from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PIL import Image

from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.comfyui.client import ComfyUIClient
from assetclaw_matting.comfyui.output_resolver import resolve_best_output
from assetclaw_matting.comfyui.workflow_patch import find_primary_save_image_node_id, find_save_image_outputs, inspect_workflow, patch_load_image, prepare_api_prompt_for_run, workflow_to_api_prompt
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.comfyui_skills import (
    run_pause,
    run_preview,
    run_delete,
    run_resume,
    run_start,
    run_status,
    run_list,
    workflow_info,
    workflow_select,
    workflows_list,
)
from assetclaw_matting.skills import comfyui_skills
from assetclaw_matting.brain.local_command_brain import LocalCommandBrain


def setup_module() -> None:
    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()


def _make_workflow(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
                "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"}},
            }
        ),
        encoding="utf-8",
    )


def _frontend_workflow() -> dict:
    return {
        "nodes": [
            {
                "id": 1,
                "type": "LoadImage",
                "title": "加载图像",
                "inputs": [{"name": "image", "localized_name": "图像", "widget": {"name": "image"}, "link": None}],
                "outputs": [{"name": "IMAGE", "localized_name": "图像"}],
                "widgets_values": ["example.png"],
            },
            {
                "id": 2,
                "type": "SaveImage",
                "title": "保存图像",
                "inputs": [
                    {"name": "images", "localized_name": "图像", "link": 1},
                    {"name": "filename_prefix", "localized_name": "文件名前缀", "widget": {"name": "filename_prefix"}, "link": None},
                ],
                "outputs": [],
                "widgets_values": ["ComfyUI"],
            },
        ],
        "links": [[1, 1, 0, 2, 0, "IMAGE"]],
    }


def _wait_done(run_id: str) -> dict:
    status = {}
    for _ in range(50):
        status = run_status(run_id, include_gpu=False)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(0.05)
    return status


def test_workflow_info_reads_api_workflow() -> None:
    workflow = Path.cwd() / "storage/debug/comfy_test_workflow.json"
    _make_workflow(workflow)

    result = workflow_info(str(workflow))

    assert result["ok"] is True
    assert result["node_count"] == 2
    assert result["load_image_nodes"][0]["id"] == "1"


def test_frontend_workflow_inspect_and_convert() -> None:
    workflow = _frontend_workflow()
    info = inspect_workflow(workflow)
    assert info["node_count"] == 2
    assert info["load_image_nodes"][0]["title"] == "加载图像"
    assert info["save_image_nodes"][0]["title"] == "保存图像"

    patched = patch_load_image(workflow, "uploaded.png")
    prompt = workflow_to_api_prompt(patched)
    assert prompt["1"]["class_type"] == "LoadImage"
    assert prompt["1"]["inputs"]["image"] == "uploaded.png"
    assert "upload" not in prompt["1"]["inputs"]
    assert prompt["2"]["inputs"]["images"] == ["1", 0]


def test_prepare_api_prompt_does_not_rewire_workflow() -> None:
    workflow = _frontend_workflow()

    prompt = prepare_api_prompt_for_run(workflow)

    assert prompt["2"]["class_type"] == "SaveImage"
    assert prompt["2"]["inputs"]["images"] == ["1", 0]


def test_find_save_image_outputs_prefers_final_output_over_temp() -> None:
    history = {
        "pid": {
            "outputs": {
                "50": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]},
                "28": {"images": [{"filename": "final.png", "subfolder": "", "type": "output"}]},
            }
        }
    }

    outputs = find_save_image_outputs(history, "pid")

    assert outputs[0]["filename"] == "final.png"
    assert outputs[0]["type"] == "output"


def test_find_save_image_outputs_uses_primary_save_image_node_not_preview_masks() -> None:
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
        "20": {"class_type": "SaveImage", "_meta": {"title": "保存图像"}, "inputs": {"filename_prefix": "final"}},
        "30": {"class_type": "SaveImage", "_meta": {"title": "预览遮罩"}, "inputs": {"filename_prefix": "mask"}},
        "31": {"class_type": "SaveImage", "_meta": {"title": "预览校色遮罩范围"}, "inputs": {"filename_prefix": "mask_scope"}},
    }
    history = {
        "pid": {
            "outputs": {
                "20": {"images": [{"filename": "final_transparent.png", "subfolder": "", "type": "output"}]},
                "30": {"images": [{"filename": "preview_mask.png", "subfolder": "", "type": "output"}]},
                "31": {"images": [{"filename": "preview_mask_scope.png", "subfolder": "", "type": "output"}]},
            }
        }
    }

    final_node_id = find_primary_save_image_node_id(workflow)
    outputs = find_save_image_outputs(history, "pid", final_save_image_node_id=final_node_id)

    assert final_node_id == "20"
    assert [item["filename"] for item in outputs] == ["final_transparent.png"]


def test_primary_save_image_node_prefers_cherry_color_restore_rgba_output() -> None:
    workflow = {
        "nodes": [
            {"id": 228, "type": "VAEDecode", "outputs": [{"name": "IMAGE"}]},
            {"id": 232, "type": "CherryItemColorRestore", "outputs": [{"name": "图像(RGBA)"}, {"name": "覆盖蒙版"}]},
            {"id": 226, "type": "JoinImageWithAlpha", "outputs": [{"name": "IMAGE"}]},
            {
                "id": 234,
                "type": "SaveImage",
                "title": "",
                "inputs": [{"name": "images", "link": 369}],
                "widgets_values": ["ComfyUI"],
            },
            {
                "id": 210,
                "type": "SaveImage",
                "title": "",
                "inputs": [{"name": "images", "link": 399}],
                "widgets_values": ["ComfyUI"],
            },
            {
                "id": 235,
                "type": "SaveImage",
                "title": "抠完图-无校色",
                "inputs": [{"name": "images", "link": 370}],
                "widgets_values": ["ComfyUI"],
            },
            {
                "id": 207,
                "type": "SaveImage",
                "title": "预览校色覆盖范围",
                "inputs": [{"name": "images", "link": 381}],
                "widgets_values": ["ComfyUI"],
            },
        ],
        "links": [
            [369, 228, 0, 234, 0, "IMAGE"],
            [399, 232, 0, 210, 0, "图像(RGBA)"],
            [370, 232, 0, 235, 0, "图像(RGBA)"],
            [381, 226, 0, 207, 0, "IMAGE"],
        ],
    }

    assert find_primary_save_image_node_id(workflow) == "210"


def test_resolve_best_output_rejects_bad_primary_save_even_if_preview_mask_is_transparent(tmp_path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    Image.new("RGB", (64, 64), (0, 0, 0)).save(output_root / "final_black.png")
    Image.new("RGBA", (64, 64), (255, 0, 0, 0)).save(output_root / "preview_mask_transparent.png")
    history = {
        "pid": {
            "outputs": {
                "20": {"images": [{"filename": "final_black.png", "subfolder": "", "type": "output"}]},
                "30": {"images": [{"filename": "preview_mask_transparent.png", "subfolder": "", "type": "output"}]},
            }
        }
    }

    with pytest.raises(ValueError, match="没有找到合格的最终透明 PNG"):
        resolve_best_output(
            history,
            "pid",
            local_path_resolver=lambda filename, subfolder, output_type: output_root / filename,
            final_save_image_node_id="20",
        )


def test_download_output_prefers_local_comfyui_file_and_preserves_alpha(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    comfy_root = Path.cwd() / "storage/debug/comfy_local_copy_root"
    source = comfy_root / "output" / "matte" / "1_00001_.png"
    target = Path.cwd() / "storage/debug/comfy_local_copy_outputs/0001.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(source)
    monkeypatch.setattr(settings, "comfyui_dir", comfy_root)

    def fail_get(*args, **kwargs):
        raise AssertionError("expected local ComfyUI file copy, not /view download")

    monkeypatch.setattr("assetclaw_matting.comfyui.client.requests.get", fail_get)

    ComfyUIClient().download_output("1_00001_.png", "matte", "output", target)

    with Image.open(target) as image:
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema() == (0, 0)


def test_resolve_best_output_prefers_existing_transparent_png_without_resolution_lock(tmp_path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    Image.new("RGB", (300, 400), (255, 255, 255)).save(output_root / "white_preview.png")
    Image.new("RGBA", (900, 1200), (255, 0, 0, 127)).save(output_root / "transparent_intermediate.png")
    Image.new("RGB", (300, 400), (0, 0, 0)).save(output_root / "black_preview.png")
    final = Image.new("RGBA", (486, 608), (255, 0, 0, 0))
    for x in range(120, 360):
        for y in range(120, 500):
            final.putpixel((x, y), (180, 40, 30, 255))
    final.save(output_root / "final_transparent.png")
    history = {
        "pid": {
            "outputs": {
                "10": {"images": [{"filename": "white_preview.png", "subfolder": "", "type": "output"}]},
                "11": {"images": [{"filename": "transparent_intermediate.png", "subfolder": "", "type": "output"}]},
                "12": {"images": [{"filename": "black_preview.png", "subfolder": "", "type": "output"}]},
                "13": {"images": [{"filename": "final_transparent.png", "subfolder": "", "type": "output"}]},
            }
        }
    }

    selected = resolve_best_output(
        history,
        "pid",
        local_path_resolver=lambda filename, subfolder, output_type: output_root / filename,
    )

    assert selected["filename"] == "final_transparent.png"


def test_resolve_best_output_rejects_black_preview_and_mask(tmp_path) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    Image.new("RGBA", (1080, 1440), (0, 0, 0, 255)).save(output_root / "black_preview.png")
    Image.new("RGBA", (1080, 1440), (255, 255, 255, 0)).save(output_root / "white_mask.png")
    mask = Image.new("RGBA", (1080, 1440), (0, 0, 0, 0))
    for x in range(360, 720):
        for y in range(360, 1080):
            mask.putpixel((x, y), (255, 255, 255, 255))
    mask.save(output_root / "mask_like.png")
    history = {
        "pid": {
            "outputs": {
                "10": {"images": [{"filename": "black_preview.png", "subfolder": "", "type": "output"}]},
                "11": {"images": [{"filename": "white_mask.png", "subfolder": "", "type": "output"}]},
                "12": {"images": [{"filename": "mask_like.png", "subfolder": "", "type": "output"}]},
            }
        }
    }

    with pytest.raises(ValueError, match="没有找到合格的最终透明 PNG"):
        resolve_best_output(
            history,
            "pid",
            local_path_resolver=lambda filename, subfolder, output_type: output_root / filename,
        )


def test_workflows_list() -> None:
    root = Path.cwd() / "storage/debug/workflows"
    _make_workflow(root / "matting_api.json")

    result = workflows_list(str(root))

    assert result["ok"] is True
    assert result["items"][0]["name"] == "matting_api.json"


def test_fake_run_start_and_status(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", True)
    workflow = Path.cwd() / "storage/debug/comfy_fake_workflow.json"
    input_dir = Path.cwd() / "storage/debug/comfy_inputs"
    output_dir = Path.cwd() / "storage/debug/comfy_outputs"
    _make_workflow(workflow)
    input_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(input_dir / "a.png")

    started = run_start(str(workflow), str(input_dir), str(output_dir))
    assert started["ok"] is True
    assert started["total"] == 1

    status = _wait_done(started["run_id"])
    assert status["ok"] is True
    assert status["input_dir"] == str(input_dir)
    assert status["output_dir"] == str(output_dir)

    text = format_skill_results([{"ok": True, "skill": "comfyui.run_status", "result": status}])
    assert "ComfyUI 管线" in text
    assert "进度" in text


def test_run_delete_hides_finished_task(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", True)
    workflow = Path.cwd() / "storage/debug/comfy_delete_workflow.json"
    input_dir = Path.cwd() / "storage/debug/comfy_delete_inputs"
    output_dir = Path.cwd() / "storage/debug/comfy_delete_outputs"
    _make_workflow(workflow)
    input_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 255, 0, 255)).save(input_dir / "a.png")

    started = run_start(str(workflow), str(input_dir), str(output_dir))
    status = _wait_done(started["run_id"])
    assert status["status"] == "DONE"

    deleted = run_delete(started["run_id"])
    assert deleted["ok"] is True
    assert deleted["status"] == "ARCHIVED"
    listed = run_list(limit=20)
    assert started["run_id"] not in {item["run_id"] for item in listed["items"]}


def test_run_status_without_id_prefers_active_run(monkeypatch) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    now = "2026-06-02T00:00:00+00:00"
    with get_connection() as conn:
        conn.execute("DELETE FROM comfyui_runs")
        conn.execute(
            """
            INSERT OR REPLACE INTO comfyui_runs
            (id, status, workflow_path, input_dir, output_dir, total, files_json, prompt_ids_json, options_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("COMFY_DONE000000", "CANCELED", "done.json", "E:\\input", "E:\\output", 1, "[]", "[]", "{}", now, now),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO comfyui_runs
            (id, status, workflow_path, input_dir, output_dir, total, files_json, prompt_ids_json, options_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("COMFY_ACTIVE000", "RUNNING", "active.json", "E:\\input", "E:\\output", 1, "[]", "[]", "{}", now, now),
        )

    status = run_status(None, include_gpu=False)

    assert status["run_id"] == "COMFY_ACTIVE000"


def test_progress_notification_is_quiet_between_large_steps() -> None:
    status = {"status": "RUNNING", "completed": 1, "total": 158}
    assert comfyui_skills._should_notify_progress(status, last_completed=0, last_status="RUNNING") is False
    status["completed"] = 8
    assert comfyui_skills._should_notify_progress(status, last_completed=0, last_status="RUNNING") is True


def test_workflow_select_and_recursive_same_structure_output(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", True)
    workflow = Path.cwd() / "storage/debug/comfy_selected_workflow.json"
    input_dir = Path.cwd() / "storage/debug/comfy_nested_inputs"
    output_dir = Path.cwd() / "storage/debug/comfy_nested_outputs"
    _make_workflow(workflow)
    (input_dir / "Jessica-happy").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (0, 255, 0)).save(input_dir / "Jessica-happy" / "pose.jpg")

    token = set_runtime_context(conversation_id="test_comfy_select")
    try:
        selected = workflow_select(str(workflow))
        assert selected["ok"] is True
        started = run_start(None, str(input_dir), str(output_dir), recursive=True, preserve_structure=True)
    finally:
        reset_runtime_context(token)

    assert started["ok"] is True
    assert started["total"] == 1
    _wait_done(started["run_id"])
    assert (output_dir / "Jessica-happy" / "pose.png").exists()


def test_run_preview_and_pause_resume(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", True)
    workflow = Path.cwd() / "storage/debug/comfy_preview_workflow.json"
    input_dir = Path.cwd() / "storage/debug/comfy_preview_inputs"
    output_dir = Path.cwd() / "storage/debug/comfy_preview_outputs"
    _make_workflow(workflow)
    input_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (0, 0, 255, 255)).save(input_dir / "a.png")

    preview = run_preview(str(workflow), str(input_dir), str(output_dir))
    assert preview["total"] == 1
    assert preview["workflow_name"] == workflow.name

    started = run_start(str(workflow), str(input_dir), str(output_dir))
    paused = run_pause(started["run_id"])
    assert paused["ok"] is True
    resumed = run_resume(started["run_id"])
    assert resumed["ok"] is True


def test_run_resume_relaunches_running_task(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    calls: list[str] = []
    monkeypatch.setattr(settings, "comfyui_fake_mode", True)
    monkeypatch.setattr(comfyui_skills, "_start_run_worker", lambda run_id: calls.append(run_id))
    monkeypatch.setattr(comfyui_skills, "_start_progress_monitor", lambda run_id: None)
    run_id = "COMFY_RESUME0001"
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO comfyui_runs
            (id, status, workflow_path, input_dir, output_dir, total, files_json, prompt_ids_json, options_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "RUNNING",
                "workflow.json",
                "E:\\input",
                "E:\\output",
                1,
                "[]",
                "[]",
                json.dumps({"prompt_map": []}),
                "2026-06-04T00:00:00+00:00",
                "2026-06-04T00:00:00+00:00",
            ),
        )

    resumed = run_resume(run_id)

    assert resumed["ok"] is True
    assert resumed["message"] == "已拉起提交 worker。"
    assert calls == [run_id]


def test_run_resume_does_not_duplicate_active_queue(monkeypatch) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    calls: list[str] = []
    monkeypatch.setattr(settings, "comfyui_fake_mode", False)
    monkeypatch.setattr(comfyui_skills, "_start_run_worker", lambda run_id: calls.append(run_id))
    monkeypatch.setattr(comfyui_skills, "_start_progress_monitor", lambda run_id: None)
    monkeypatch.setattr(
        "assetclaw_matting.comfyui.client.comfyui_client.get_queue",
        lambda: {"queue_running": [{"prompt_id": "p"}], "queue_pending": []},
    )
    run_id = "COMFY_QUEUE00001"
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO comfyui_runs
            (id, status, workflow_path, input_dir, output_dir, total, files_json, prompt_ids_json, options_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "RUNNING",
                "workflow.json",
                "E:\\input",
                "E:\\output",
                1,
                "[]",
                "[]",
                json.dumps({"prompt_map": []}),
                "2026-06-04T00:00:00+00:00",
                "2026-06-04T00:00:00+00:00",
            ),
        )

    resumed = run_resume(run_id)

    assert resumed["message"] == "任务已经在 ComfyUI 队列中运行。"
    assert calls == []


def test_local_router_resumes_vague_start_and_cancels_named_run() -> None:
    brain = LocalCommandBrain()

    resume = brain._infer_tool_calls("为什么这个没开始啊")
    cancel = brain._infer_tool_calls("取消终止这个任务 COMFY_ABCDEF123456")
    vague_start = brain._infer_tool_calls("开始抠图啊")

    assert resume[0].skill == "agent.diagnose"
    assert cancel[0].skill == "comfyui.run_cancel"
    assert cancel[0].arguments["run_id"] == "COMFY_ABCDEF123456"
    assert vague_start[0].skill == "comfyui.run_resume"

from __future__ import annotations

import json
from pathlib import Path

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.brain.schemas import BrainMessage
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import get_connection, init_db
from assetclaw_matting.skills.agent_ops_skills import current_work, diagnose
from assetclaw_matting.skills.registry import call_skill, get_skill_meta


def setup_module() -> None:
    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()


def test_agent_skills_registered_and_router() -> None:
    assert get_skill_meta("agent.current_work")
    assert get_skill_meta("agent.diagnose")
    result = call_skill("agent.current_work", {"include_gpu": False}, requested_by="test")
    assert result["ok"] is True

    brain = LocalCommandBrain()
    response = brain.handle_message(BrainMessage(text="你看看现在什么情况，为什么这个没开始啊"))
    assert response.tool_calls
    assert response.tool_calls[0].skill == "agent.diagnose"


def test_agent_diagnose_detects_stalled_comfyui(monkeypatch) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM comfyui_runs")
        conn.execute("DELETE FROM cherry_runs")
        conn.execute("DELETE FROM frame_runs")
        conn.execute("DELETE FROM pipeline_runs")
        conn.execute(
            """
            INSERT INTO comfyui_runs
            (id, status, workflow_path, input_dir, output_dir, total, files_json, prompt_ids_json, options_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "COMFY_TESTSTALL",
                "RUNNING",
                "E:\\workflow.json",
                "E:\\input",
                "E:\\output",
                2,
                json.dumps(["E:\\input\\0001.png", "E:\\input\\0002.png"]),
                "[]",
                json.dumps({"prompt_map": []}),
                "2026-06-04T01:00:00+00:00",
                "2026-06-04T01:00:00+00:00",
            ),
        )

    from assetclaw_matting.skills import comfyui_skills

    monkeypatch.setattr(comfyui_skills, "queue_status", lambda: {"ok": True, "running": [], "pending": []})
    payload = diagnose(include_gpu=False)

    topics = {item["topic"] for item in payload["findings"]}
    assert "comfyui_worker_stalled" in topics
    assert payload["next_actions"] == [{"skill": "comfyui.run_resume", "arguments": {"run_id": "COMFY_TESTSTALL"}}]

    text = format_skill_results([{"ok": True, "skill": "agent.diagnose", "result": payload}])
    assert "建议下一步" in text
    assert "comfyui.run_resume" in text


def test_agent_current_work_includes_active_runs() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM comfyui_runs")
        conn.execute(
            """
            INSERT INTO comfyui_runs
            (id, status, workflow_path, input_dir, output_dir, total, files_json, prompt_ids_json, options_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "COMFY_TESTACTIVE",
                "RUNNING",
                "E:\\workflow.json",
                "E:\\input",
                "E:\\output",
                1,
                json.dumps(["E:\\input\\0001.png"]),
                "[]",
                json.dumps({"prompt_map": []}),
                "2026-06-04T01:00:00+00:00",
                "2026-06-04T01:00:00+00:00",
            ),
        )
    payload = current_work(include_gpu=False)
    assert payload["ok"] is True
    assert any(item.get("run_id") == "COMFY_TESTACTIVE" for item in payload["active"])
    text = format_skill_results([{"ok": True, "skill": "agent.current_work", "result": payload}])
    assert "最近错误" not in text


def test_agent_current_work_formats_direct_media_as_table(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings

    storage = tmp_path / "storage"
    video_run = storage / "direct_video_runs" / "VID_TABLE" / "status.json"
    image_run = storage / "direct_image_runs" / "IMG_TABLE" / "status.json"
    (video_run.parent / "matte" / "video_01").mkdir(parents=True)
    (image_run.parent / "smooth" / "image_01").mkdir(parents=True)
    for index in range(3):
        (video_run.parent / "matte" / "video_01" / f"{index:04d}.png").write_bytes(b"x")
    (image_run.parent / "smooth" / "image_01" / "source.png").write_bytes(b"x")
    video_run.write_text(
        json.dumps(
            {
                "id": "VID_TABLE",
                "status": "RUNNING",
                "stage": "matting",
                "run_label": "思考.mp4",
                "created_at": "2026-07-14T10:00:00",
                "updated_at": "2026-07-14T10:10:00",
                "videos": [
                    {
                        "name": "01_思考.mp4",
                        "frame_count": 10,
                        "matte_dir": str(video_run.parent / "matte" / "video_01"),
                        "smooth_dir": str(video_run.parent / "smooth" / "video_01"),
                        "cherry_output_size": "384x512",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    image_run.write_text(
        json.dumps(
            {
                "id": "IMG_TABLE",
                "status": "DONE",
                "stage": "done",
                "run_label": "头像.png",
                "created_at": "2026-07-14T10:00:00",
                "updated_at": "2026-07-14T10:11:00",
                "images": [
                    {
                        "name": "头像.png",
                        "matte_dir": str(image_run.parent / "matte" / "image_01"),
                        "smooth_dir": str(image_run.parent / "smooth" / "image_01"),
                        "cherry_output_size": "256x256",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "storage_dir", storage)

    payload = current_work(include_gpu=False)
    text = format_skill_results([{"ok": True, "skill": "agent.current_work", "result": payload}])

    assert "类型 | 文件 | 任务 | 阶段 | 进度 | 规格 | 说明" in text
    assert "视频 | 思考.mp4 | VID_TABLE | 抠图中 | 抠图 3/10 | 384x512" in text
    assert "图片 | 头像.png | IMG_TABLE | 完成 | 已完成 | 256x256" in text


def test_agent_current_work_dedupes_same_media_name(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings

    storage = tmp_path / "storage"
    old_run = storage / "direct_video_runs" / "VID_OLD" / "status.json"
    new_run = storage / "direct_video_runs" / "VID_NEW" / "status.json"
    for path in (old_run, new_run):
        (path.parent / "matte" / "video_01").mkdir(parents=True)
        (path.parent / "matte" / "video_01" / "0001.png").write_bytes(b"x")
    old_run.write_text(
        json.dumps(
            {
                "id": "VID_OLD",
                "status": "RUNNING",
                "stage": "matting",
                "created_at": "2026-07-14T10:00:00",
                "updated_at": "2026-07-14T10:00:00",
                "videos": [{"name": "7月13日思考-1_5.mp4", "frame_count": 111, "matte_dir": str(old_run.parent / "matte" / "video_01"), "cherry_output_size": "384x512"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    new_run.write_text(
        json.dumps(
            {
                "id": "VID_NEW",
                "status": "RUNNING",
                "stage": "matting",
                "created_at": "2026-07-14T10:05:00",
                "updated_at": "2026-07-14T10:05:00",
                "videos": [{"name": "7月13日思考-1_5.mp4", "frame_count": 111, "matte_dir": str(new_run.parent / "matte" / "video_01"), "cherry_output_size": "384x512"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "storage_dir", storage)

    payload = current_work(include_gpu=False)
    text = format_skill_results([{"ok": True, "skill": "agent.current_work", "result": payload}])

    assert text.count("7月13日思考-1_5.mp4") == 1
    assert "VID_NEW" in text
    assert "VID_OLD" not in text


def test_agent_current_work_filters_by_date_query_and_detail(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.config import settings

    storage = tmp_path / "storage"
    july_11 = storage / "direct_video_runs" / "VID_711" / "status.json"
    july_13 = storage / "direct_video_runs" / "VID_713" / "status.json"
    for path in (july_11, july_13):
        (path.parent / "matte" / "video_01").mkdir(parents=True)
        (path.parent / "smooth" / "video_01").mkdir(parents=True)
    for index in range(5):
        (july_13.parent / "matte" / "video_01" / f"{index:04d}.png").write_bytes(b"x")
    july_11.write_text(
        json.dumps(
            {
                "id": "VID_711",
                "status": "DONE",
                "stage": "done",
                "run_label": "7月11日待机.mp4",
                "created_at": "2026-07-11T09:00:00",
                "updated_at": "2026-07-11T09:30:00",
                "videos": [{"name": "7月11日待机.mp4", "frame_count": 20, "matte_dir": str(july_11.parent / "matte" / "video_01"), "smooth_dir": str(july_11.parent / "smooth" / "video_01"), "cherry_output_size": "256x256"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    july_13.write_text(
        json.dumps(
            {
                "id": "VID_713",
                "status": "RUNNING",
                "stage": "matting",
                "run_label": "source_2.mp4",
                "created_at": "2026-07-13T14:00:00",
                "updated_at": "2026-07-13T14:10:00",
                "videos": [{"name": "source_2.mp4", "frame_count": 43, "matte_dir": str(july_13.parent / "matte" / "video_01"), "smooth_dir": str(july_13.parent / "smooth" / "video_01"), "cherry_output_size": "256x256"}],
                "children": {"comfyui_run_id": "COMFY_DETAIL"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "storage_dir", storage)

    summary = current_work(include_gpu=False, date_start="2026-07-11", date_end="2026-07-13")
    summary_text = format_skill_results([{"ok": True, "skill": "agent.current_work", "result": summary}])
    assert "7月11日待机.mp4" in summary_text
    assert "source_2.mp4" in summary_text
    assert "运行中/抠图" in summary_text
    assert "RUNNING/matting" not in summary_text

    detail = current_work(include_gpu=False, date_start="2026-07-13", date_end="2026-07-13", query="source_2.mp4", detail=True)
    detail_text = format_skill_results([{"ok": True, "skill": "agent.current_work", "result": detail}])
    assert "视频任务详情：source_2.mp4（VID_713）" in detail_text
    assert "- 抽帧：43 帧" in detail_text
    assert "- 抠图：5/43，COMFY_DETAIL" in detail_text
    assert "- 后处理：0/43" in detail_text


def test_agent_diagnose_truncates_recent_errors() -> None:
    long_error = "LLM Proxy failed: " + ("very long json " * 80)
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO skill_calls
            (request_id, skill, arguments_json, result_json, ok, error, requested_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("err-test", "image.ocr", "{}", "{}", 0, long_error, "test", "2026-06-04T01:00:00+00:00"),
        )
    payload = diagnose(include_gpu=False)
    latest = payload["recent_errors"][0]["error"]
    assert len(latest) <= 223
    text = format_skill_results([{"ok": True, "skill": "agent.diagnose", "result": payload}])
    assert len(text) < 3000

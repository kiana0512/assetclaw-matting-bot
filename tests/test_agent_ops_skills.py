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
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
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

from __future__ import annotations

import json
import time
from pathlib import Path

from PIL import Image

from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.runtime_context import reset_runtime_context, set_runtime_context
from assetclaw_matting.skills.shared_matting_skills import shared_matting_start, shared_matting_status


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
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


def test_shared_matting_stages_local_and_syncs_back(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", True)
    workflow = Path("E:/assetclaw-matting-bot/storage/debug/shared_matting_workflow.json")
    shared_input = Path("E:/assetclaw-matting-bot/storage/debug/shared_input")
    shared_output = Path("E:/assetclaw-matting-bot/storage/debug/shared_output")
    _make_workflow(workflow)
    shared_input.mkdir(parents=True, exist_ok=True)
    shared_output.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(shared_input / "a.png")
    notifications: list[str] = []

    monkeypatch.setattr("assetclaw_matting.services.shared_matting_service.send_text", lambda chat_id, text: notifications.append(text))
    token = set_runtime_context(chat_id="chat_test")
    try:
        started = shared_matting_start(
            shared_input_dir=str(shared_input),
            shared_output_dir=str(shared_output),
            workflow_path=str(workflow),
            notify_interval_seconds=10,
        )
    finally:
        reset_runtime_context(token)

    assert started["ok"] is True
    for _ in range(30):
        status = shared_matting_status(started["run_id"])
        if status["status"] == "DONE":
            break
        time.sleep(0.1)

    assert shared_matting_status(started["run_id"])["status"] == "DONE"
    assert (shared_output / "a.png").exists()
    assert notifications

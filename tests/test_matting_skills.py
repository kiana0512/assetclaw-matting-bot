from __future__ import annotations

from pathlib import Path

from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.registry import call_skill


def setup_module() -> None:
    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()


def test_matting_batch_fake_lifecycle() -> None:
    created = call_skill(
        "matting.batch_create",
        {
            "input_dir": ".\\storage\\batch_inputs",
            "output_dir": ".\\storage\\batch_outputs",
        },
        requested_by="test",
    )
    assert created["ok"] is True
    batch_id = created["result"]["batch_id"]

    started = call_skill("matting.batch_start", {"batch_id": batch_id}, requested_by="test")
    assert started["ok"] is True
    assert started["result"]["status"] == "RUNNING"

    paused = call_skill("matting.batch_pause", {"batch_id": batch_id}, requested_by="test")
    assert paused["result"]["status"] == "PAUSED"

    resumed = call_skill("matting.batch_resume", {"batch_id": batch_id}, requested_by="test")
    assert resumed["result"]["status"] == "RUNNING"

    status = call_skill("matting.batch_status", {"batch_id": batch_id}, requested_by="test")
    assert status["ok"] is True
    assert status["result"]["batch_id"] == batch_id

    canceled = call_skill("matting.batch_cancel", {"batch_id": batch_id}, requested_by="test")
    assert canceled["result"]["status"] == "CANCELED"

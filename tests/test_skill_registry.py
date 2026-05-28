"""Tests for skills/registry.py: manifest and skill dispatch."""
from __future__ import annotations

import pytest

import assetclaw_matting.db.sqlite as db_module


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    import assetclaw_matting.config as cfg_module
    from assetclaw_matting.config import Settings

    s = Settings(
        storage_dir=str(tmp_path / "storage"),
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        allowed_roots="",
        comfyui_fake_mode=True,
    )
    monkeypatch.setattr(cfg_module, "settings", s)
    s.ensure_dirs()

    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.db.schema import create_tables
    db_path = s.data_dir / "test.db"
    init_db(db_path)
    create_tables()
    yield s
    db_module._db_path = None


# ── Manifest ─────────────────────────────────────────────────────────────────

def test_manifest_contains_required_skills():
    from assetclaw_matting.skills.registry import get_manifest
    manifest = get_manifest()
    names = {s["name"] for s in manifest["available_skills"]}
    required = {
        "batch.create", "batch.start", "batch.status", "batch.list", "batch.cancel",
        "queue.status", "task.status", "task.list_failed",
        "worker.status", "comfyui.status", "file.list_allowed", "log.tail",
    }
    assert required.issubset(names), f"Missing: {required - names}"


def test_manifest_has_machine_id():
    from assetclaw_matting.skills.registry import get_manifest
    manifest = get_manifest()
    assert "machine_id" in manifest
    assert manifest["agent_runs_on_gpu"] is False


# ── Skill dispatch ────────────────────────────────────────────────────────────

def test_queue_status_returns_ok():
    from assetclaw_matting.skills.registry import call_skill
    result = call_skill("queue.status", {}, requested_by="test")
    assert result["ok"] is True
    assert "queued_tasks" in result["result"]


def test_comfyui_status_fake_mode():
    from assetclaw_matting.skills.registry import call_skill
    result = call_skill("comfyui.status", {}, requested_by="test")
    assert result["ok"] is True
    assert result["result"]["fake_mode"] is True


def test_unknown_skill_returns_error():
    from assetclaw_matting.skills.registry import call_skill
    result = call_skill("does.not.exist", {}, requested_by="test")
    assert result["ok"] is False
    assert "Unknown skill" in result["error"]


def test_future_skill_returns_not_implemented():
    from assetclaw_matting.skills.registry import call_skill
    result = call_skill("frame.extract", {}, requested_by="test")
    assert result["ok"] is True
    assert result["result"]["status"] == "not_implemented"


def test_skill_call_logged_to_db():
    from assetclaw_matting.skills.registry import call_skill
    from assetclaw_matting.db.sqlite import get_connection
    call_skill("queue.status", {}, requested_by="pytest", request_id="req-test-1")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM skill_calls WHERE request_id = 'req-test-1'"
        ).fetchone()
    assert row is not None
    assert row["skill"] == "queue.status"
    assert row["ok"] == 1
    assert row["requested_by"] == "pytest"


def test_worker_status_returns_ok():
    from assetclaw_matting.skills.registry import call_skill
    result = call_skill("worker.status", {})
    assert result["ok"] is True
    assert "running_tasks" in result["result"]

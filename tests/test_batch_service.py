"""Tests for batch creation, start, cancel, and completion callbacks."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import assetclaw_matting.config as cfg_module
from assetclaw_matting.config import Settings


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    """Redirect all storage to a temp directory and init a fresh DB."""
    storage = tmp_path / "storage"
    data = tmp_path / "data"
    logs = tmp_path / "logs"

    new_settings = Settings(
        storage_dir=str(storage),
        data_dir=str(data),
        log_dir=str(logs),
        allowed_roots="",  # no restriction in tests
        comfyui_fake_mode=True,
    )
    monkeypatch.setattr(cfg_module, "settings", new_settings)

    # Patch settings in every submodule that imported it at module load time
    for mod_name in (
        "assetclaw_matting.services.file_store",
        "assetclaw_matting.services.batch_service",
        "assetclaw_matting.services.task_service",
        "assetclaw_matting.db.batch_repo",
        "assetclaw_matting.db.task_repo",
    ):
        import importlib
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "settings"):
            monkeypatch.setattr(mod, "settings", new_settings)

    new_settings.ensure_dirs()

    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.db.schema import create_tables
    import assetclaw_matting.db.sqlite as db_module
    db_path = data / "test.db"
    init_db(db_path)
    create_tables()
    yield new_settings
    db_module._db_path = None


@pytest.fixture()
def input_dir(tmp_path):
    """Create a temp input dir with 3 PNG files."""
    d = tmp_path / "inputs"
    d.mkdir()
    from PIL import Image
    for i in range(3):
        img = Image.new("RGB", (64, 64), color=(i * 80, 100, 200))
        img.save(str(d / f"image_{i:02d}.png"))
    return d


@pytest.fixture()
def output_dir(tmp_path):
    d = tmp_path / "outputs"
    d.mkdir()
    return d


# ── create_batch ──────────────────────────────────────────────────────────────

def test_create_batch_returns_correct_counts(input_dir, output_dir):
    from assetclaw_matting.services.batch_service import create_batch

    batch = create_batch(input_dir, output_dir, workflow_type="matting_v1")
    assert batch.total_count == 3
    assert batch.queued_count == 3
    assert batch.status.value == "CREATED"


def test_create_batch_tasks_have_correct_paths(input_dir, output_dir):
    from assetclaw_matting.services.batch_service import create_batch
    from assetclaw_matting.db.task_repo import list_tasks

    batch = create_batch(input_dir, output_dir)
    tasks = list_tasks(batch_id=batch.id)
    assert len(tasks) == 3
    for task in tasks:
        assert task.input_path is not None
        assert Path(task.input_path).exists()
        assert task.output_path is not None
        assert task.output_path.endswith("_matting.png")


def test_create_batch_fails_on_missing_input_dir(output_dir):
    from assetclaw_matting.services.batch_service import create_batch

    with pytest.raises(ValueError, match="input_dir does not exist"):
        create_batch("/nonexistent/path", output_dir)


def test_create_batch_fails_on_empty_dir(tmp_path, output_dir):
    from assetclaw_matting.services.batch_service import create_batch

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="No supported images"):
        create_batch(empty, output_dir)


# ── start_batch ───────────────────────────────────────────────────────────────

def test_start_batch_changes_status(input_dir, output_dir):
    from assetclaw_matting.services.batch_service import create_batch, start_batch

    batch = create_batch(input_dir, output_dir)
    started = start_batch(batch.id)
    assert started.status.value == "RUNNING"
    assert started.started_at is not None


def test_start_batch_fails_if_already_running(input_dir, output_dir):
    from assetclaw_matting.services.batch_service import create_batch, start_batch

    batch = create_batch(input_dir, output_dir)
    start_batch(batch.id)
    with pytest.raises(ValueError, match="expected CREATED"):
        start_batch(batch.id)


# ── cancel_batch ──────────────────────────────────────────────────────────────

def test_cancel_batch_cancels_queued_tasks(input_dir, output_dir):
    from assetclaw_matting.services.batch_service import cancel_batch, create_batch, start_batch
    from assetclaw_matting.db.task_repo import list_tasks

    batch = create_batch(input_dir, output_dir)
    start_batch(batch.id)
    canceled = cancel_batch(batch.id)
    assert canceled.status.value == "CANCELED"
    tasks = list_tasks(batch_id=batch.id, status="CANCELED")
    assert len(tasks) == 3


# ── on_task_completed ─────────────────────────────────────────────────────────

def test_batch_completes_when_all_tasks_succeed(input_dir, output_dir):
    from assetclaw_matting.services.batch_service import (
        create_batch, on_task_completed, on_task_started, start_batch,
    )
    from assetclaw_matting.services.task_service import mark_running, mark_succeeded
    from assetclaw_matting.db.batch_repo import get_batch
    from assetclaw_matting.db.task_repo import list_tasks
    from assetclaw_matting.models.task_models import TaskStatus

    batch = create_batch(input_dir, output_dir)
    start_batch(batch.id)
    tasks = list_tasks(batch_id=batch.id)

    for task in tasks:
        mark_running(task.id, "test-worker")
        on_task_started(task.id)
        mark_succeeded(task.id, task.output_path or "/tmp/out.png")
        on_task_completed(task.id, TaskStatus.SUCCEEDED)

    final = get_batch(batch.id)
    assert final is not None
    assert final.status.value == "SUCCEEDED"
    assert final.succeeded_count == 3
    assert final.finished_at is not None


def test_batch_fails_when_any_task_fails(input_dir, output_dir):
    from assetclaw_matting.services.batch_service import (
        create_batch, on_task_completed, on_task_started, start_batch,
    )
    from assetclaw_matting.services.task_service import mark_failed, mark_running, mark_succeeded
    from assetclaw_matting.db.batch_repo import get_batch
    from assetclaw_matting.db.task_repo import list_tasks
    from assetclaw_matting.models.task_models import TaskStatus

    batch = create_batch(input_dir, output_dir)
    start_batch(batch.id)
    tasks = list_tasks(batch_id=batch.id)

    for i, task in enumerate(tasks):
        mark_running(task.id, "test-worker")
        on_task_started(task.id)
        if i == 0:
            mark_failed(task.id, "GPU OOM")
            on_task_completed(task.id, TaskStatus.FAILED)
        else:
            mark_succeeded(task.id, task.output_path or "/tmp/out.png")
            on_task_completed(task.id, TaskStatus.SUCCEEDED)

    final = get_batch(batch.id)
    assert final is not None
    assert final.status.value == "FAILED"
    assert final.failed_count == 1
    assert final.succeeded_count == 2

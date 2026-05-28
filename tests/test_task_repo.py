"""Tests for db/task_repo.py using an in-memory temp database."""
from __future__ import annotations

import pytest

import assetclaw_matting.db.sqlite as db_module
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db import task_repo
from assetclaw_matting.models.task_models import Task, TaskStatus


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db_module.init_db(db_path)
    create_tables()
    yield
    db_module._db_path = None


def _make_task(**kwargs) -> Task:
    defaults = dict(
        id="task-001",
        batch_id="BATCH_TEST01",
        source="batch",
        workflow_type="matting_v1",
        status=TaskStatus.QUEUED,
        input_path="/tmp/input.png",
        output_path="/tmp/output.png",
        original_filename="input.png",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return Task(**defaults)


def _make_batch(batch_id: str = "BATCH_TEST01", status: str = "RUNNING") -> None:
    """Insert a minimal batch row so the JOIN in get_next_queued_task works."""
    from assetclaw_matting.db.sqlite import get_connection
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO batches (id, input_dir, output_dir, status, created_at, updated_at) "
            "VALUES (?, '/in', '/out', ?, ?, ?)",
            (batch_id, status, now, now),
        )


def test_insert_and_get():
    task = _make_task()
    task_repo.insert_task(task)
    fetched = task_repo.get_task("task-001")
    assert fetched is not None
    assert fetched.id == "task-001"
    assert fetched.status == TaskStatus.QUEUED


def test_update_fields():
    task = _make_task()
    task_repo.insert_task(task)
    task_repo.update_task_fields("task-001", status=TaskStatus.RUNNING, worker_id="w1")
    fetched = task_repo.get_task("task-001")
    assert fetched.status == TaskStatus.RUNNING
    assert fetched.worker_id == "w1"


def test_get_next_queued_requires_running_batch():
    _make_batch(status="CREATED")  # not RUNNING → task should not be returned
    task = _make_task()
    task_repo.insert_task(task)
    assert task_repo.get_next_queued_task() is None


def test_get_next_queued_task():
    _make_batch(status="RUNNING")
    t1 = _make_task(id="task-001", created_at="2024-01-01T00:00:00+00:00")
    t2 = _make_task(id="task-002", created_at="2024-01-02T00:00:00+00:00")
    task_repo.insert_task(t1)
    task_repo.insert_task(t2)
    nxt = task_repo.get_next_queued_task()
    assert nxt is not None
    assert nxt.id == "task-001"


def test_count_tasks_by_status():
    task_repo.insert_task(_make_task(id="t1", status=TaskStatus.QUEUED))
    task_repo.insert_task(_make_task(id="t2", status=TaskStatus.QUEUED))
    task_repo.insert_task(_make_task(id="t3", status=TaskStatus.SUCCEEDED))
    counts = task_repo.count_tasks_by_status("BATCH_TEST01")
    assert counts["QUEUED"] == 2
    assert counts["SUCCEEDED"] == 1


def test_insert_event_and_seen():
    task_repo.insert_event("ev-1", "im.message.receive_v1", "msg-1", {"foo": "bar"})
    assert task_repo.event_id_seen("ev-1") is True
    assert task_repo.event_id_seen("ev-2") is False

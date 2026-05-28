from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from assetclaw_matting.db import task_repo
from assetclaw_matting.models.task_models import Task, TaskStatus
from assetclaw_matting.services.file_store import write_task_json

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_task(
    batch_id: str,
    source: str = "batch",
    workflow_type: str = "matting_v1",
    input_path: Optional[str] = None,
    output_path: Optional[str] = None,
    original_filename: Optional[str] = None,
) -> Task:
    task = Task(
        id=str(uuid.uuid4()),
        batch_id=batch_id,
        source=source,
        workflow_type=workflow_type,
        status=TaskStatus.QUEUED,
        input_path=input_path,
        output_path=output_path,
        original_filename=original_filename,
        created_at=_now(),
        updated_at=_now(),
    )
    task_repo.insert_task(task)
    write_task_json(task)
    return task


def mark_running(task_id: str, worker_id: str) -> Task:
    task = task_repo.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    if task.status != TaskStatus.QUEUED:
        raise ValueError(f"Task {task_id} is {task.status}, expected QUEUED")
    task_repo.update_task_fields(
        task_id,
        status=TaskStatus.RUNNING,
        worker_id=worker_id,
        started_at=_now(),
    )
    updated = task_repo.get_task(task_id)
    assert updated is not None
    write_task_json(updated)
    return updated


def mark_succeeded(task_id: str, output_path: str) -> Task:
    task_repo.update_task_fields(
        task_id,
        status=TaskStatus.SUCCEEDED,
        output_path=output_path,
        finished_at=_now(),
    )
    task = task_repo.get_task(task_id)
    assert task is not None
    write_task_json(task)
    log.info("Task succeeded: %s output=%s", task_id, output_path)
    return task


def mark_failed(task_id: str, error: str) -> Task:
    task_repo.update_task_fields(
        task_id,
        status=TaskStatus.FAILED,
        error=error[:2000],
        finished_at=_now(),
    )
    task = task_repo.get_task(task_id)
    assert task is not None
    write_task_json(task)
    log.warning("Task failed: %s error=%s", task_id, error[:200])
    return task


def mark_canceled(task_id: str) -> Task:
    task = task_repo.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    if task.status not in (TaskStatus.QUEUED,):
        raise ValueError(f"Cannot cancel task in status {task.status}")
    task_repo.update_task_fields(task_id, status=TaskStatus.CANCELED, finished_at=_now())
    updated = task_repo.get_task(task_id)
    assert updated is not None
    write_task_json(updated)
    return updated

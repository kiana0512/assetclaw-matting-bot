from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from assetclaw_matting.db import batch_repo, task_repo
from assetclaw_matting.models.batch_models import Batch, BatchStatus
from assetclaw_matting.models.task_models import TaskStatus
from assetclaw_matting.services import file_store, notification_service, task_service

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_batch_id() -> str:
    short = str(uuid.uuid4()).replace("-", "")[:12].upper()
    return f"BATCH_{short}"


# ── Create ────────────────────────────────────────────────────────────────────

def create_batch(
    input_dir: str | Path,
    output_dir: str | Path,
    workflow_type: str = "matting_v1",
    notify_chat_id: Optional[str] = None,
    note: Optional[str] = None,
    source: str = "batch",
) -> Batch:
    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()

    if not input_dir.is_dir():
        raise ValueError(f"input_dir does not exist: {input_dir}")

    # Validate allowed roots
    file_store.validate_allowed_path(input_dir)
    file_store.validate_allowed_path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Scan images
    images = file_store.scan_images(input_dir)
    if not images:
        raise ValueError(f"No supported images found in {input_dir}")

    batch_id = _new_batch_id()
    batch = Batch(
        id=batch_id,
        source=source,
        workflow_type=workflow_type,
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        total_count=len(images),
        queued_count=len(images),
        status=BatchStatus.CREATED,
        notify_chat_id=notify_chat_id,
        note=note,
        created_at=_now(),
        updated_at=_now(),
    )
    batch_repo.insert_batch(batch)

    # Create one task per image
    for img_path in images:
        out_path = file_store.compute_output_path(output_dir, img_path)
        task_service.create_task(
            batch_id=batch_id,
            source=source,
            workflow_type=workflow_type,
            input_path=str(img_path),
            output_path=str(out_path),
            original_filename=img_path.name,
        )

    log.info(
        "Batch created: %s workflow=%s count=%d",
        batch_id, workflow_type, len(images),
    )

    notification_service.notify_batch_created(batch)
    return batch


# ── Start ─────────────────────────────────────────────────────────────────────

def start_batch(batch_id: str) -> Batch:
    batch = batch_repo.get_batch(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    if batch.status != BatchStatus.CREATED:
        raise ValueError(f"Batch {batch_id} is {batch.status}, expected CREATED")

    batch_repo.update_batch_fields(
        batch_id,
        status=BatchStatus.RUNNING,
        started_at=_now(),
    )
    updated = batch_repo.get_batch(batch_id)
    assert updated is not None
    log.info("Batch started: %s", batch_id)
    notification_service.notify_batch_started(updated)
    return updated


# ── Cancel ────────────────────────────────────────────────────────────────────

def cancel_batch(batch_id: str) -> Batch:
    batch = batch_repo.get_batch(batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    if batch.status in (BatchStatus.SUCCEEDED, BatchStatus.FAILED, BatchStatus.CANCELED):
        raise ValueError(f"Batch {batch_id} is already in terminal state {batch.status}")

    # Cancel all QUEUED tasks
    canceled_tasks = task_repo.cancel_queued_tasks_in_batch(batch_id)
    # RUNNING tasks finish naturally and will call on_task_completed
    batch_repo.update_batch_fields(
        batch_id,
        status=BatchStatus.CANCELED,
        canceled_count=batch.canceled_count + canceled_tasks,
        queued_count=0,
        finished_at=_now(),
    )
    updated = batch_repo.get_batch(batch_id)
    assert updated is not None
    log.info("Batch canceled: %s (canceled %d QUEUED tasks)", batch_id, canceled_tasks)
    notification_service.notify_batch_canceled(updated)
    return updated


# ── Task completion callback ──────────────────────────────────────────────────

def on_task_completed(task_id: str, new_status: TaskStatus) -> None:
    """Called after a task transitions to SUCCEEDED, FAILED, or CANCELED.

    Updates batch counters and checks for batch completion.
    """
    task = task_repo.get_task(task_id)
    if task is None or not task.batch_id:
        return

    batch_id = task.batch_id

    # Adjust counts
    if new_status == TaskStatus.SUCCEEDED:
        batch_repo.increment_batch_counter(batch_id, "succeeded_count")
    elif new_status == TaskStatus.FAILED:
        batch_repo.increment_batch_counter(batch_id, "failed_count")
    elif new_status == TaskStatus.CANCELED:
        batch_repo.increment_batch_counter(batch_id, "canceled_count")

    batch_repo.increment_batch_counter(batch_id, "running_count", -1)

    batch = batch_repo.get_batch(batch_id)
    if batch is None:
        return

    # Progress notification
    if notification_service.should_send_progress(batch):
        notification_service.notify_batch_progress(batch)

    # Check completion
    if batch.is_finished and batch.status == BatchStatus.RUNNING:
        final_status = (
            BatchStatus.SUCCEEDED if batch.failed_count == 0
            else BatchStatus.FAILED
        )
        batch_repo.update_batch_fields(
            batch_id,
            status=final_status,
            finished_at=_now(),
        )
        final_batch = batch_repo.get_batch(batch_id)
        assert final_batch is not None
        log.info("Batch completed: %s status=%s", batch_id, final_status)
        notification_service.notify_batch_completed(final_batch)


def on_task_started(task_id: str) -> None:
    """Called after a task transitions from QUEUED to RUNNING."""
    task = task_repo.get_task(task_id)
    if task is None or not task.batch_id:
        return
    batch_repo.increment_batch_counter(task.batch_id, "running_count")
    batch_repo.increment_batch_counter(task.batch_id, "queued_count", -1)

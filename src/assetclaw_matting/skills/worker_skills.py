"""Skill implementations for worker and task management."""
from __future__ import annotations

from typing import Any, Optional


def queue_status() -> dict[str, Any]:
    from assetclaw_matting.db import batch_repo, task_repo
    stats = task_repo.queue_stats()
    return {
        "running_batches": batch_repo.running_batch_count(),
        "queued_tasks": stats["QUEUED"],
        "running_tasks": stats["RUNNING"],
        "failed_tasks": stats["FAILED"],
    }


def worker_status() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.task_repo import list_tasks, queue_stats
    stats = queue_stats()
    running = list_tasks(status="RUNNING", limit=5)
    return {
        "worker_id": settings.worker_id,
        "active_workers": list({t.worker_id for t in running if t.worker_id}),
        "running_tasks": stats["RUNNING"],
        "queued_tasks": stats["QUEUED"],
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
    }


def task_status(task_id: str) -> dict[str, Any]:
    from assetclaw_matting.db.task_repo import get_task
    t = get_task(task_id)
    if t is None:
        raise ValueError(f"Task not found: {task_id}")
    return t.model_dump()


def task_list_failed(
    batch_id: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    from assetclaw_matting.db.task_repo import list_tasks
    tasks = list_tasks(batch_id=batch_id or None, status="FAILED", limit=int(limit))
    return {
        "failed_tasks": [
            {
                "id": t.id,
                "batch_id": t.batch_id,
                "original_filename": t.original_filename,
                "error": t.error,
                "finished_at": t.finished_at,
            }
            for t in tasks
        ],
        "count": len(tasks),
    }

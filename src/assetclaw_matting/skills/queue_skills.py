"""Queue status skills — separated for clarity."""
from __future__ import annotations

from typing import Any


def queue_status() -> dict[str, Any]:
    from assetclaw_matting.db import batch_repo, task_repo
    stats = task_repo.queue_stats()
    return {
        "running_batches": batch_repo.running_batch_count(),
        "queued_tasks": stats["QUEUED"],
        "running_tasks": stats["RUNNING"],
        "failed_tasks": stats["FAILED"],
    }

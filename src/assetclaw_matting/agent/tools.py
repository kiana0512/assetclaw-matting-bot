"""Concrete tool implementations called by the agent harness."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def batch_create(
    input_dir: str,
    output_dir: str,
    workflow_type: str = "matting_v1",
    notify_chat_id: str = "",
    note: str = "",
) -> dict[str, Any]:
    from assetclaw_matting.services.batch_service import create_batch
    b = create_batch(
        input_dir=input_dir,
        output_dir=output_dir,
        workflow_type=workflow_type,
        notify_chat_id=notify_chat_id or None,
        note=note or None,
    )
    return {"batch_id": b.id, "total_count": b.total_count, "status": b.status}


def batch_start(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.services.batch_service import start_batch
    b = start_batch(batch_id)
    return {"batch_id": b.id, "status": b.status}


def batch_status(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.db.batch_repo import get_batch
    b = get_batch(batch_id)
    if b is None:
        return {"error": f"Batch not found: {batch_id}"}
    return b.model_dump()


def batch_list(limit: int = 10, status: str = "") -> dict[str, Any]:
    from assetclaw_matting.db.batch_repo import list_batches
    batches = list_batches(status=status or None, limit=limit)
    return {"batches": [b.model_dump() for b in batches]}


def batch_cancel(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.services.batch_service import cancel_batch
    b = cancel_batch(batch_id)
    return {"batch_id": b.id, "status": b.status}


def queue_status() -> dict[str, Any]:
    from assetclaw_matting.db import task_repo, batch_repo
    stats = task_repo.queue_stats()
    return {
        "running_batches": batch_repo.running_batch_count(),
        "queued_tasks": stats["QUEUED"],
        "running_tasks": stats["RUNNING"],
        "failed_tasks": stats["FAILED"],
    }


def worker_status() -> dict[str, Any]:
    from assetclaw_matting.db import task_repo
    stats = task_repo.queue_stats()
    running = task_repo.list_tasks(status="RUNNING", limit=5)
    return {
        "active_workers": list({t.worker_id for t in running if t.worker_id}),
        "running_tasks": stats["RUNNING"],
        "queued_tasks": stats["QUEUED"],
    }


def comfyui_status() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    if settings.comfyui_fake_mode:
        return {"status": "fake_online", "fake_mode": True}
    from assetclaw_matting.comfyui.client import comfyui_client
    try:
        info = comfyui_client.check_health()
        return {"status": "online", "fake_mode": False, "info": info}
    except Exception as exc:
        return {"status": "offline", "error": str(exc)}


def task_list_failed(batch_id: str, limit: int = 20) -> dict[str, Any]:
    from assetclaw_matting.db.task_repo import list_tasks
    tasks = list_tasks(batch_id=batch_id, status="FAILED", limit=limit)
    return {
        "failed_tasks": [
            {"id": t.id, "original_filename": t.original_filename, "error": t.error}
            for t in tasks
        ]
    }


def task_retry_failed(batch_id: str) -> dict[str, Any]:
    return {"error": "task_retry_failed is not yet implemented"}


def log_summarize(log_type: str = "gateway", lines: int = 50) -> dict[str, Any]:
    return {"error": "log_summarize is not yet implemented"}


# Registry mapping tool name → callable
TOOL_REGISTRY: dict[str, Any] = {
    "batch_create": batch_create,
    "batch_start": batch_start,
    "batch_status": batch_status,
    "batch_list": batch_list,
    "batch_cancel": batch_cancel,
    "queue_status": queue_status,
    "worker_status": worker_status,
    "comfyui_status": comfyui_status,
    "task_list_failed": task_list_failed,
    "task_retry_failed": task_retry_failed,
    "log_summarize": log_summarize,
}

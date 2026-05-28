from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from assetclaw_matting.config import settings
from assetclaw_matting.db import task_repo
from assetclaw_matting.models.task_models import TaskStatus
from assetclaw_matting.models.worker_models import (
    WorkerFailedRequest,
    WorkerNextTaskResponse,
    WorkerStartedRequest,
    WorkerSucceededRequest,
    WorkerTaskPayload,
)
from assetclaw_matting.services import batch_service, task_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/worker", tags=["worker"])


def _verify_token(x_worker_token: str = Header(...)) -> None:
    if x_worker_token != settings.worker_token:
        raise HTTPException(status_code=401, detail="Invalid worker token")


# ── GET /worker/tasks/next ────────────────────────────────────────────────────

@router.get("/tasks/next", dependencies=[Depends(_verify_token)])
async def get_next_task() -> WorkerNextTaskResponse:
    task = task_repo.get_next_queued_task()
    if task is None:
        return WorkerNextTaskResponse(task=None)
    return WorkerNextTaskResponse(
        task=WorkerTaskPayload(
            task_id=task.id,
            batch_id=task.batch_id,
            workflow_type=task.workflow_type,
            input_path=task.input_path or "",
            output_path=task.output_path or "",
            source=task.source,
        )
    )


# ── POST /worker/tasks/{task_id}/started ──────────────────────────────────────

@router.post("/tasks/{task_id}/started", dependencies=[Depends(_verify_token)])
async def mark_started(task_id: str, body: WorkerStartedRequest) -> JSONResponse:
    try:
        task = task_service.mark_running(task_id, body.worker_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # Update batch counters
    batch_service.on_task_started(task_id)
    return JSONResponse({"ok": True, "task_id": task.id, "status": task.status})


# ── GET /worker/tasks/{task_id}/input ─────────────────────────────────────────
# Convenience endpoint for remote workers that can't access input_path directly.

@router.get("/tasks/{task_id}/input", dependencies=[Depends(_verify_token)])
async def get_task_input(task_id: str) -> FileResponse:
    task = task_repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.input_path:
        raise HTTPException(status_code=404, detail="No input path recorded for task")
    input_path = Path(task.input_path)
    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"Input file not found: {input_path}")
    return FileResponse(
        path=str(input_path),
        media_type="image/png",
        filename=input_path.name,
    )


# ── POST /worker/tasks/{task_id}/succeeded ────────────────────────────────────

@router.post("/tasks/{task_id}/succeeded", dependencies=[Depends(_verify_token)])
async def submit_succeeded(task_id: str, body: WorkerSucceededRequest) -> JSONResponse:
    task = task_repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task_service.mark_succeeded(task_id, body.output_path)
    batch_service.on_task_completed(task_id, TaskStatus.SUCCEEDED)
    return JSONResponse({"ok": True})


# ── POST /worker/tasks/{task_id}/failed ───────────────────────────────────────

@router.post("/tasks/{task_id}/failed", dependencies=[Depends(_verify_token)])
async def submit_failed(task_id: str, body: WorkerFailedRequest) -> JSONResponse:
    task = task_repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task_service.mark_failed(task_id, body.error)
    batch_service.on_task_completed(task_id, TaskStatus.FAILED)
    return JSONResponse({"ok": True})

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assetclaw_matting.db import batch_repo, task_repo
from assetclaw_matting.models.batch_models import BatchCreate
from assetclaw_matting.services import batch_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


# ── Batches ───────────────────────────────────────────────────────────────────

@router.get("/batches")
async def list_batches(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    batches = batch_repo.list_batches(status=status, limit=min(limit, 100), offset=offset)
    return {"batches": [b.model_dump() for b in batches], "count": len(batches)}


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str) -> dict:
    b = batch_repo.get_batch(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {"batch": b.model_dump()}


@router.post("/batches/create")
async def create_batch(body: BatchCreate) -> JSONResponse:
    try:
        b = batch_service.create_batch(
            input_dir=body.input_dir,
            output_dir=body.output_dir,
            workflow_type=body.workflow_type,
            notify_chat_id=body.notify_chat_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("Batch create failed")
        raise HTTPException(status_code=500, detail=str(exc))

    # Return first 10 task_ids
    tasks = task_repo.list_tasks(batch_id=b.id, limit=10)
    return JSONResponse({
        "ok": True,
        "batch_id": b.id,
        "total_count": b.total_count,
        "task_ids": [t.id for t in tasks],
    })


@router.post("/batches/{batch_id}/start")
async def start_batch(batch_id: str) -> JSONResponse:
    try:
        b = batch_service.start_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"ok": True, "batch_id": b.id, "status": b.status})


@router.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str) -> JSONResponse:
    try:
        b = batch_service.cancel_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse({"ok": True, "batch_id": b.id, "status": b.status})


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    tasks = task_repo.list_tasks(
        batch_id=batch_id,
        status=status,
        limit=min(limit, 200),
        offset=offset,
    )
    return {"tasks": [t.model_dump() for t in tasks], "count": len(tasks)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    t = task_repo.get_task(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Task not found")
    logs = task_repo.get_task_logs(task_id)
    return {"task": t.model_dump(), "logs": logs}


# ── Queue ─────────────────────────────────────────────────────────────────────

@router.get("/queue")
async def queue_status() -> dict:
    stats = task_repo.queue_stats()
    running_batches = batch_repo.running_batch_count()
    return {
        "running_batches": running_batches,
        "queued_tasks": stats["QUEUED"],
        "running_tasks": stats["RUNNING"],
        "failed_tasks": stats["FAILED"],
    }


# ── Worker status ─────────────────────────────────────────────────────────────

@router.get("/worker/status")
async def worker_status() -> dict:
    stats = task_repo.queue_stats()
    # Find most recent worker_id from running tasks
    running_tasks = task_repo.list_tasks(status="RUNNING", limit=5)
    worker_ids = list({t.worker_id for t in running_tasks if t.worker_id})
    return {
        "active_workers": worker_ids,
        "running_tasks": stats["RUNNING"],
        "queued_tasks": stats["QUEUED"],
    }


# ── ComfyUI status ────────────────────────────────────────────────────────────

@router.get("/comfyui/status")
async def comfyui_status() -> dict:
    from assetclaw_matting.config import settings
    if settings.comfyui_fake_mode:
        return {"status": "fake_online", "fake_mode": True, "url": settings.comfyui_url}
    from assetclaw_matting.comfyui.client import comfyui_client
    try:
        info = comfyui_client.check_health()
        return {"status": "online", "fake_mode": False, "url": settings.comfyui_url, "info": info}
    except Exception as exc:
        return {"status": "offline", "fake_mode": False, "error": str(exc), "url": settings.comfyui_url}

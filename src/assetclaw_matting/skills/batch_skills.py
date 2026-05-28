"""Skill implementations for batch management."""
from __future__ import annotations

from typing import Any, Optional


def batch_create(
    input_dir: str,
    output_dir: str,
    workflow_type: str = "matting_v1",
    notify_chat_id: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    from assetclaw_matting.services.batch_service import create_batch
    b = create_batch(
        input_dir=input_dir,
        output_dir=output_dir,
        workflow_type=workflow_type,
        notify_chat_id=notify_chat_id or None,
        note=note or None,
    )
    return {
        "batch_id": b.id,
        "total_count": b.total_count,
        "status": b.status,
        "input_dir": b.input_dir,
        "output_dir": b.output_dir,
    }


def batch_start(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.services.batch_service import start_batch
    b = start_batch(batch_id)
    return {"batch_id": b.id, "status": b.status, "started_at": b.started_at}


def batch_status(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.db.batch_repo import get_batch
    b = get_batch(batch_id)
    if b is None:
        raise ValueError(f"Batch not found: {batch_id}")
    return b.model_dump()


def batch_list(limit: int = 10, status: Optional[str] = None) -> dict[str, Any]:
    from assetclaw_matting.db.batch_repo import list_batches
    batches = list_batches(status=status, limit=int(limit))
    return {"batches": [b.model_dump() for b in batches], "count": len(batches)}


def batch_cancel(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.services.batch_service import cancel_batch
    b = cancel_batch(batch_id)
    return {"batch_id": b.id, "status": b.status, "canceled_at": b.finished_at}

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class Task(BaseModel):
    id: str
    batch_id: Optional[str] = None
    source: str = "batch"
    workflow_type: str = "matting_v1"
    status: TaskStatus = TaskStatus.QUEUED
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    original_filename: Optional[str] = None
    error: Optional[str] = None
    worker_id: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

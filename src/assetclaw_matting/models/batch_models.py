from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class BatchStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class Batch(BaseModel):
    id: str
    source: str = "batch"
    workflow_type: str = "matting_v1"
    input_dir: str
    output_dir: str
    total_count: int = 0
    queued_count: int = 0
    running_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    canceled_count: int = 0
    status: BatchStatus = BatchStatus.CREATED
    notify_chat_id: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    note: Optional[str] = None

    @property
    def completed_count(self) -> int:
        return self.succeeded_count + self.failed_count + self.canceled_count

    @property
    def is_finished(self) -> bool:
        return self.total_count > 0 and self.completed_count >= self.total_count


class BatchCreate(BaseModel):
    input_dir: str
    output_dir: str
    workflow_type: str = "matting_v1"
    notify_chat_id: Optional[str] = None
    note: Optional[str] = None

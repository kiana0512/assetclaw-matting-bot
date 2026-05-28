from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class WorkerTaskPayload(BaseModel):
    task_id: str
    batch_id: Optional[str]
    workflow_type: str
    input_path: str
    output_path: str
    source: str = "batch"


class WorkerNextTaskResponse(BaseModel):
    task: Optional[WorkerTaskPayload] = None


class WorkerStartedRequest(BaseModel):
    worker_id: str


class WorkerSucceededRequest(BaseModel):
    worker_id: str
    output_path: str


class WorkerFailedRequest(BaseModel):
    error: str
    worker_id: str


WorkerNextTaskResponse.model_rebuild()

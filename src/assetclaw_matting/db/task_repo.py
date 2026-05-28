from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assetclaw_matting.db.sqlite import get_connection
from assetclaw_matting.models.task_models import Task, TaskStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(**dict(row))


# ── Tasks ─────────────────────────────────────────────────────────────────────

def insert_task(task: Task) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, batch_id, source, workflow_type, status,
                input_path, output_path, original_filename,
                error, worker_id, created_at, updated_at, started_at, finished_at
            ) VALUES (
                :id, :batch_id, :source, :workflow_type, :status,
                :input_path, :output_path, :original_filename,
                :error, :worker_id, :created_at, :updated_at, :started_at, :finished_at
            )
            """,
            task.model_dump(),
        )


def get_task(task_id: str) -> Optional[Task]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return _row_to_task(row) if row else None


def update_task_fields(task_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = task_id
    with get_connection() as conn:
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = :id", fields)


def get_next_queued_task() -> Optional[Task]:
    """Return the earliest QUEUED task whose batch is in RUNNING status."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT tasks.* FROM tasks
            JOIN batches ON tasks.batch_id = batches.id
            WHERE tasks.status = 'QUEUED'
              AND batches.status = 'RUNNING'
            ORDER BY tasks.created_at ASC
            LIMIT 1
            """
        ).fetchone()
    return _row_to_task(row) if row else None


def list_tasks(
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    workflow_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Task]:
    conditions: list[str] = []
    params: list[Any] = []
    if batch_id:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if workflow_type:
        conditions.append("workflow_type = ?")
        params.append(workflow_type)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [_row_to_task(r) for r in rows]


def count_tasks_by_status(batch_id: str) -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks WHERE batch_id = ? GROUP BY status",
            (batch_id,),
        ).fetchall()
    result: dict[str, int] = {s: 0 for s in ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED")}
    for r in rows:
        result[r["status"]] = r["cnt"]
    return result


def cancel_queued_tasks_in_batch(batch_id: str) -> int:
    """Cancel all QUEUED tasks in a batch. Returns count of canceled tasks."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status = 'CANCELED', updated_at = ? WHERE batch_id = ? AND status = 'QUEUED'",
            (_now(), batch_id),
        )
    return cur.rowcount


def queue_stats() -> dict[str, int]:
    """Global queue stats across all batches."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks WHERE status IN ('QUEUED','RUNNING','FAILED') GROUP BY status"
        ).fetchall()
    result: dict[str, int] = {"QUEUED": 0, "RUNNING": 0, "FAILED": 0}
    for r in rows:
        result[r["status"]] = r["cnt"]
    return result


# ── Events ────────────────────────────────────────────────────────────────────

def insert_event(event_id: str, event_type: str, message_id: str, raw_json: Any) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO events (event_id, event_type, message_id, raw_json, created_at) VALUES (?,?,?,?,?)",
            (event_id, event_type, message_id, json.dumps(raw_json), _now()),
        )


def event_id_seen(event_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM events WHERE event_id = ? LIMIT 1", (event_id,)
        ).fetchone()
    return row is not None


# ── Task logs ─────────────────────────────────────────────────────────────────

def append_task_log(task_id: str, level: str, message: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO task_logs (task_id, level, message, created_at) VALUES (?,?,?,?)",
            (task_id, level, message, _now()),
        )


def get_task_logs(task_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM task_logs WHERE task_id = ? ORDER BY id ASC", (task_id,)
        ).fetchall()
    return [dict(r) for r in rows]

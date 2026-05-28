from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assetclaw_matting.db.sqlite import get_connection
from assetclaw_matting.models.batch_models import Batch, BatchStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_batch(row: sqlite3.Row) -> Batch:
    return Batch(**dict(row))


def insert_batch(batch: Batch) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO batches (
                id, source, workflow_type, input_dir, output_dir,
                total_count, queued_count, running_count, succeeded_count,
                failed_count, canceled_count, status, notify_chat_id,
                created_at, updated_at, started_at, finished_at, note
            ) VALUES (
                :id, :source, :workflow_type, :input_dir, :output_dir,
                :total_count, :queued_count, :running_count, :succeeded_count,
                :failed_count, :canceled_count, :status, :notify_chat_id,
                :created_at, :updated_at, :started_at, :finished_at, :note
            )
            """,
            batch.model_dump(),
        )


def get_batch(batch_id: str) -> Optional[Batch]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM batches WHERE id = ?", (batch_id,)
        ).fetchone()
    return _row_to_batch(row) if row else None


def update_batch_fields(batch_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = batch_id
    with get_connection() as conn:
        conn.execute(f"UPDATE batches SET {set_clause} WHERE id = :id", fields)


def increment_batch_counter(batch_id: str, column: str, delta: int = 1) -> None:
    with get_connection() as conn:
        conn.execute(
            f"UPDATE batches SET {column} = {column} + ?, updated_at = ? WHERE id = ?",
            (delta, _now(), batch_id),
        )


def list_batches(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Batch]:
    conditions: list[str] = []
    params: list[Any] = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM batches {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [_row_to_batch(r) for r in rows]


def running_batch_count() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM batches WHERE status = 'RUNNING'"
        ).fetchone()
    return row["cnt"] if row else 0

from __future__ import annotations

import sqlite3
import logging

from assetclaw_matting.db.sqlite import get_connection

log = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS batches (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'batch',
    workflow_type   TEXT NOT NULL DEFAULT 'matting_v1',
    input_dir       TEXT NOT NULL,
    output_dir      TEXT NOT NULL,
    total_count     INTEGER NOT NULL DEFAULT 0,
    queued_count    INTEGER NOT NULL DEFAULT 0,
    running_count   INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    canceled_count  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'CREATED',
    notify_chat_id  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_batches_status  ON batches(status);
CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at);

CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    batch_id          TEXT,
    source            TEXT NOT NULL DEFAULT 'batch',
    workflow_type     TEXT NOT NULL DEFAULT 'matting_v1',
    status            TEXT NOT NULL DEFAULT 'QUEUED',
    input_path        TEXT,
    output_path       TEXT,
    original_filename TEXT,
    error             TEXT,
    worker_id         TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    started_at        TEXT,
    finished_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_batch_id ON tasks(batch_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created  ON tasks(created_at);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT,
    event_type  TEXT,
    message_id  TEXT,
    raw_json    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id);

CREATE TABLE IF NOT EXISTS task_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'INFO',
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_logs_task_id ON task_logs(task_id);

CREATE TABLE IF NOT EXISTS agent_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT,
    chat_id         TEXT,
    sender_id       TEXT,
    message_text    TEXT,
    agent_reply     TEXT,
    tool_calls_json TEXT,
    created_at      TEXT NOT NULL
);

-- Audit log for every Skill Gateway call
CREATE TABLE IF NOT EXISTS skill_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT,
    skill           TEXT NOT NULL,
    arguments_json  TEXT,
    result_json     TEXT,
    ok              INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    requested_by    TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_calls_skill     ON skill_calls(skill);
CREATE INDEX IF NOT EXISTS idx_skill_calls_created   ON skill_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_skill_calls_requested ON skill_calls(requested_by);
"""

_MIGRATIONS = [
    ("tasks", "batch_id", "TEXT"),
]


def create_tables() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA_SQL)
        _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, col_def in _MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            log.info("Migration: added column %s.%s", table, column)
        except sqlite3.OperationalError:
            pass

from __future__ import annotations

from assetclaw_matting.db.sqlite import get_connection


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    skill TEXT,
    arguments_json TEXT,
    result_json TEXT,
    ok INTEGER,
    error TEXT,
    requested_by TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS brain_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT,
    channel TEXT,
    conversation_id TEXT,
    user_id TEXT,
    message_text TEXT,
    response_text TEXT,
    tool_calls_json TEXT,
    raw_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT,
    key TEXT,
    value TEXT,
    source TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    conversation_id TEXT PRIMARY KEY,
    summary_text TEXT,
    compacted_until_id INTEGER,
    source_count INTEGER,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    status TEXT,
    input_dir TEXT,
    output_dir TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    level TEXT,
    message TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS feishu_event_dedup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    message_id TEXT,
    chat_id TEXT,
    open_id TEXT,
    trace_id TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    user_id TEXT,
    skill TEXT,
    arguments_json TEXT,
    status TEXT,
    created_at TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS character_resolution_questions (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    chat_id TEXT,
    user_id TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    outbound_message_id TEXT,
    waiting_started_at TEXT,
    next_action_at TEXT,
    deadline_at TEXT,
    reminder_count INTEGER NOT NULL DEFAULT 0,
    last_reminded_at TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    failed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS character_resolutions (
    unit_id TEXT PRIMARY KEY,
    run_kind TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_dir TEXT NOT NULL,
    item_index INTEGER NOT NULL,
    group_key TEXT,
    conversation_id TEXT NOT NULL,
    chat_id TEXT,
    user_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    question_id TEXT,
    question_token TEXT NOT NULL,
    status TEXT NOT NULL,
    character_id TEXT,
    resolution_method TEXT,
    confidence REAL,
    evidence_json TEXT,
    catalog_revision TEXT,
    reference_source_path TEXT,
    reference_snapshot_path TEXT,
    reference_sha256 TEXT,
    resolved_by_message_id TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(run_kind, run_id, item_index),
    UNIQUE(conversation_id, question_token),
    FOREIGN KEY(question_id) REFERENCES character_resolution_questions(id)
);

CREATE INDEX IF NOT EXISTS idx_character_resolutions_pending
ON character_resolutions(conversation_id, user_id, status);

CREATE INDEX IF NOT EXISTS idx_character_resolutions_run
ON character_resolutions(run_kind, run_id, status);

CREATE TABLE IF NOT EXISTS character_reference_snapshots (
    unit_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    target_width INTEGER NOT NULL,
    target_height INTEGER NOT NULL,
    character_id TEXT NOT NULL,
    catalog_revision TEXT NOT NULL,
    reference_source_path TEXT NOT NULL,
    reference_snapshot_path TEXT NOT NULL,
    reference_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(unit_id, profile),
    FOREIGN KEY(unit_id) REFERENCES character_resolutions(unit_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_character_reference_snapshots_unit
ON character_reference_snapshots(unit_id, profile);

CREATE TABLE IF NOT EXISTS comfyui_runs (
    id TEXT PRIMARY KEY,
    status TEXT,
    workflow_path TEXT,
    input_dir TEXT,
    output_dir TEXT,
    total INTEGER,
    files_json TEXT,
    prompt_ids_json TEXT,
    options_json TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS shared_matting_runs (
    id TEXT PRIMARY KEY,
    status TEXT,
    workflow_path TEXT,
    shared_input_dir TEXT,
    shared_output_dir TEXT,
    local_input_dir TEXT,
    local_output_dir TEXT,
    comfyui_run_id TEXT,
    total INTEGER,
    copied_in INTEGER,
    synced_out INTEGER,
    chat_id TEXT,
    notify_interval_seconds INTEGER,
    error TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS cherry_runs (
    id TEXT PRIMARY KEY,
    status TEXT,
    input_dir TEXT,
    output_dir TEXT,
    total INTEGER,
    completed INTEGER,
    failed INTEGER,
    files_json TEXT,
    options_json TEXT,
    error TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS frame_runs (
    id TEXT PRIMARY KEY,
    status TEXT,
    config_path TEXT,
    download_dir TEXT,
    export_dir TEXT,
    total_records INTEGER,
    processed_records INTEGER,
    fps INTEGER,
    diff_threshold REAL,
    options_json TEXT,
    error TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    status TEXT,
    input_dir TEXT,
    frame_output_dir TEXT,
    matte_output_dir TEXT,
    smooth_output_dir TEXT,
    workflow_path TEXT,
    frame_run_id TEXT,
    comfyui_run_id TEXT,
    cherry_run_id TEXT,
    current_step TEXT,
    options_json TEXT,
    error TEXT,
    created_at TEXT,
    updated_at TEXT
);
"""

# Columns added after initial table creation — applied via migration
_DEDUP_MIGRATIONS = [
    ("open_id", "TEXT"),
    ("trace_id", "TEXT"),
    ("updated_at", "TEXT"),
]

_CHARACTER_QUESTION_MIGRATIONS = [
    ("waiting_started_at", "TEXT"),
    ("next_action_at", "TEXT"),
    ("deadline_at", "TEXT"),
    ("reminder_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_reminded_at", "TEXT"),
    ("lease_owner", "TEXT"),
    ("lease_until", "TEXT"),
    ("failed_at", "TEXT"),
]


def create_tables() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
    _migrate_feishu_event_dedup()
    _migrate_character_resolution_questions()


def _migrate_feishu_event_dedup() -> None:
    """Add columns to feishu_event_dedup that were added after initial release."""
    with get_connection() as conn:
        existing = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(feishu_event_dedup)"
            ).fetchall()
        }
        for col, definition in _DEDUP_MIGRATIONS:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE feishu_event_dedup ADD COLUMN {col} {definition}"
                )


def _migrate_character_resolution_questions() -> None:
    """Add persisted reminder scheduling fields to existing installations."""

    with get_connection() as conn:
        existing = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(character_resolution_questions)"
            ).fetchall()
        }
        for col, definition in _CHARACTER_QUESTION_MIGRATIONS:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE character_resolution_questions ADD COLUMN {col} {definition}"
                )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_character_resolution_questions_due
            ON character_resolution_questions(status, next_action_at, lease_until)
            """
        )

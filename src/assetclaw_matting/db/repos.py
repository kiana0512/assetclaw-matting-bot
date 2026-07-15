from __future__ import annotations

from datetime import datetime, timezone
import json
import time
import uuid
from typing import Any

from assetclaw_matting.db.sqlite import get_connection
from assetclaw_matting.skills.security import redact_secrets


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_memory_compaction_notify_at: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Feishu event dedup
# ---------------------------------------------------------------------------

def try_insert_event_dedup(
    event_id: str,
    message_id: str,
    chat_id: str,
    open_id: str,
    trace_id: str = "",
) -> bool:
    """
    Insert dedup record. Returns True if new, False if duplicate.
    Deduplicates on event_id (primary) and message_id (fallback).
    """
    now = _now()
    # message_id fallback when event_id is absent
    if not event_id and message_id:
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM feishu_event_dedup WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
        if existing:
            return False

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO feishu_event_dedup
                    (event_id, message_id, chat_id, open_id, trace_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (event_id, message_id, chat_id, open_id, trace_id, now, now),
            )
        return True
    except Exception:
        # UNIQUE constraint on event_id = duplicate
        return False


def update_event_dedup_status(event_id: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE feishu_event_dedup SET status = ?, updated_at = ? WHERE event_id = ? OR message_id = ?",
            (status, _now(), event_id, event_id),
        )


def get_last_error_summary() -> str | None:
    """Return a one-line summary of the most recent failed skill call, or None."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT skill, error, created_at FROM skill_calls
            WHERE ok = 0 AND error != ''
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    return f"{row['skill']} - {row['error'][:80]} @ {row['created_at']}"


def insert_skill_call(
    request_id: str,
    skill: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    ok: bool,
    error: str | None,
    requested_by: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO skill_calls
            (request_id, skill, arguments_json, result_json, ok, error, requested_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                skill,
                redact_secrets(json.dumps(arguments, ensure_ascii=False, default=str)),
                redact_secrets(json.dumps(result, ensure_ascii=False, default=str)),
                1 if ok else 0,
                redact_secrets(error or ""),
                requested_by,
                _now(),
            ),
        )


def insert_brain_message(
    provider: str,
    channel: str,
    conversation_id: str,
    user_id: str,
    message_text: str,
    response_text: str,
    tool_calls_json: str,
    raw_json: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO brain_messages
            (provider, channel, conversation_id, user_id, message_text, response_text, tool_calls_json, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                channel,
                conversation_id,
                user_id,
                redact_secrets(message_text),
                redact_secrets(response_text),
                redact_secrets(tool_calls_json),
                redact_secrets(raw_json),
                _now(),
            ),
        )
    try:
        from assetclaw_matting.brain.memory_compactor import compact_conversation_if_needed

        compacted = compact_conversation_if_needed(conversation_id)
        from assetclaw_matting.config import settings

        if compacted and bool(getattr(settings, "brain_memory_compact_notify_feishu", False)):
            _notify_memory_compacted()
    except Exception:
        # Compaction is a cost-control optimization. A failure here must not
        # break the user-facing message flow.
        pass


def get_recent_brain_messages(
    conversation_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, provider, channel, user_id, message_text, response_text, tool_calls_json, created_at
            FROM brain_messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, max(1, min(limit, 30))),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def count_brain_messages(conversation_id: str) -> int:
    if not conversation_id:
        return 0
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM brain_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return int(row["count"] if row else 0)


def get_oldest_brain_messages(conversation_id: str, limit: int) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, provider, channel, user_id, message_text, response_text, tool_calls_json, created_at
            FROM brain_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (conversation_id, max(1, min(limit, 1000))),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_brain_messages_through_id(conversation_id: str, max_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM brain_messages WHERE conversation_id = ? AND id <= ?",
            (conversation_id, max_id),
        )


def get_conversation_summary(conversation_id: str) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT conversation_id, summary_text, compacted_until_id, source_count, updated_at
            FROM conversation_summaries
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def upsert_conversation_summary(
    conversation_id: str,
    summary_text: str,
    compacted_until_id: int,
    source_count: int,
) -> None:
    now = _now()
    clean_summary = redact_secrets(summary_text)
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT conversation_id FROM conversation_summaries WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE conversation_summaries
                SET summary_text = ?, compacted_until_id = ?, source_count = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (clean_summary, compacted_until_id, source_count, now, conversation_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO conversation_summaries
                (conversation_id, summary_text, compacted_until_id, source_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, clean_summary, compacted_until_id, source_count, now, now),
            )


def _notify_memory_compacted() -> None:
    from assetclaw_matting.runtime_context import get_runtime_context

    ctx = get_runtime_context()
    if ctx.get("channel") != "feishu" or not ctx.get("chat_id"):
        return
    chat_id = str(ctx["chat_id"])
    now = time.monotonic()
    if now - _memory_compaction_notify_at.get(chat_id, 0.0) < 60:
        return
    try:
        from assetclaw_matting.feishu.client import feishu_client

        feishu_client.send_text_to_chat(
            chat_id,
            "上下文已整理，会继续接着聊。",
        )
        _memory_compaction_notify_at[chat_id] = now
    except Exception:
        pass


def upsert_memory_note(scope: str, key: str, value: str, source: str = "manual") -> None:
    now = _now()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM memory_notes WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE memory_notes SET value = ?, source = ?, updated_at = ? WHERE id = ?",
                (redact_secrets(value), source, now, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO memory_notes (scope, key, value, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (scope, key, redact_secrets(value), source, now, now),
            )


def list_memory_notes(scope: str = "global", limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT scope, key, value, source, updated_at
            FROM memory_notes
            WHERE scope = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (scope, max(1, min(limit, 100))),
        ).fetchall()
    return [dict(row) for row in rows]


def create_pending_confirmation(
    conversation_id: str,
    user_id: str,
    skill: str,
    arguments: dict[str, Any],
) -> str:
    confirmation_id = uuid.uuid4().hex[:10]
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pending_confirmations
            (id, conversation_id, user_id, skill, arguments_json, status, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, datetime(?, '+10 minutes'))
            """,
            (
                confirmation_id,
                conversation_id,
                user_id,
                skill,
                redact_secrets(json.dumps(arguments, ensure_ascii=False, default=str)),
                now,
                now,
            ),
        )
    return confirmation_id


def get_latest_pending_confirmation(conversation_id: str, user_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, conversation_id, user_id, skill, arguments_json, created_at, expires_at
            FROM pending_confirmations
            WHERE conversation_id = ?
              AND user_id = ?
              AND status = 'pending'
              AND datetime(expires_at) > datetime('now')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (conversation_id, user_id),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["arguments"] = json.loads(item.pop("arguments_json") or "{}")
    return item


def get_pending_confirmation_by_id(
    confirmation_id: str,
    conversation_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, conversation_id, user_id, skill, arguments_json, created_at, expires_at
            FROM pending_confirmations
            WHERE id = ?
              AND conversation_id = ?
              AND user_id = ?
              AND status = 'pending'
              AND datetime(expires_at) > datetime('now')
            LIMIT 1
            """,
            (confirmation_id, conversation_id, user_id),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["arguments"] = json.loads(item.pop("arguments_json") or "{}")
    return item


def mark_pending_confirmation(confirmation_id: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE pending_confirmations SET status = ? WHERE id = ?",
            (status, confirmation_id),
        )


def claim_pending_confirmation(confirmation_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE pending_confirmations SET status = 'executing' WHERE id = ? AND status = 'pending'",
            (confirmation_id,),
        )
        return cursor.rowcount > 0


def supersede_similar_pending_confirmations(
    conversation_id: str,
    user_id: str,
    skill: str,
    arguments: dict[str, Any],
    keep_id: str,
) -> int:
    args_json = redact_secrets(json.dumps(arguments, ensure_ascii=False, default=str))
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE pending_confirmations
            SET status = 'superseded'
            WHERE conversation_id = ?
              AND user_id = ?
              AND skill = ?
              AND arguments_json = ?
              AND id != ?
              AND status = 'pending'
            """,
            (conversation_id, user_id, skill, args_json, keep_id),
        )
        return cursor.rowcount

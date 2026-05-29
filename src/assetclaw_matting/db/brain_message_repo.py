"""Repository for brain_messages audit table."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from assetclaw_matting.db.sqlite import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_brain_message(
    provider: str,
    channel: str,
    conversation_id: str,
    user_id: str,
    message_text: str,
    response_text: str,
    tool_calls_json: str = "",
    raw_json: str = "",
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO brain_messages "
            "(provider, channel, conversation_id, user_id, message_text, "
            "response_text, tool_calls_json, raw_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                provider, channel, conversation_id, user_id,
                message_text, response_text, tool_calls_json, raw_json, _now(),
            ),
        )


def list_brain_messages(
    provider: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if provider:
        conditions.append("provider = ?")
        params.append(provider)
    if conversation_id:
        conditions.append("conversation_id = ?")
        params.append(conversation_id)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(min(limit, 500))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, provider, channel, conversation_id, user_id, "
            f"message_text, response_text, created_at "
            f"FROM brain_messages {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]

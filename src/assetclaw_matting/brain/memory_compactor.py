from __future__ import annotations

from typing import Any


def compact_conversation_if_needed(conversation_id: str) -> bool:
    if not conversation_id:
        return False

    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import (
        count_brain_messages,
        delete_brain_messages_through_id,
        get_conversation_summary,
        get_oldest_brain_messages,
        upsert_conversation_summary,
    )

    if not settings.brain_memory_enabled or not settings.brain_memory_compact_enabled:
        return False

    keep = max(2, settings.brain_memory_compact_keep_messages)
    threshold = max(keep + 1, settings.brain_memory_compact_after_messages)
    total = count_brain_messages(conversation_id)
    if total <= threshold:
        return False

    rows_to_compact = total - keep
    old_rows = get_oldest_brain_messages(conversation_id, rows_to_compact)
    if not old_rows:
        return False

    previous = get_conversation_summary(conversation_id)
    new_summary = _merge_summary(
        previous.get("summary_text", "") if previous else "",
        old_rows,
        max_chars=max(400, settings.brain_memory_compact_max_chars),
    )
    compacted_until_id = max(int(row["id"]) for row in old_rows)
    source_count = (previous.get("source_count", 0) if previous else 0) + len(old_rows)

    upsert_conversation_summary(
        conversation_id=conversation_id,
        summary_text=new_summary,
        compacted_until_id=compacted_until_id,
        source_count=source_count,
    )
    delete_brain_messages_through_id(conversation_id, compacted_until_id)
    return True


def _merge_summary(previous_summary: str, rows: list[dict[str, Any]], max_chars: int) -> str:
    lines: list[str] = []
    if previous_summary.strip():
        lines.append(previous_summary.strip())

    for row in rows:
        user_text = _clean(row.get("message_text", ""))
        response_text = _clean(row.get("response_text", ""))
        if user_text:
            lines.append(f"用户：{user_text}")
        if response_text:
            lines.append(f"助手：{response_text}")

    compacted = _dedupe_keep_order(lines)
    text = "\n".join(compacted)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:].lstrip()


def _clean(value: str) -> str:
    value = " ".join(str(value or "").split())
    return value[:300]


def _dedupe_keep_order(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result

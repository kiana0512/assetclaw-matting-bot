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


def compact_conversation(
    conversation_id: str,
    keep_messages: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    if not conversation_id:
        return {"ok": False, "error": "conversation_id is required"}

    from assetclaw_matting.config import settings
    from assetclaw_matting.db.repos import (
        count_brain_messages,
        delete_brain_messages_through_id,
        get_conversation_summary,
        get_oldest_brain_messages,
        upsert_conversation_summary,
    )

    keep = max(2, int(keep_messages or settings.brain_memory_compact_keep_messages))
    total = count_brain_messages(conversation_id)
    if total <= keep:
        return {"ok": True, "conversation_id": conversation_id, "compacted": False, "total": total, "kept": total}

    rows_to_compact = total - keep
    old_rows = get_oldest_brain_messages(conversation_id, rows_to_compact)
    if not old_rows:
        return {"ok": True, "conversation_id": conversation_id, "compacted": False, "total": total, "kept": total}

    previous = get_conversation_summary(conversation_id)
    limit = max(400, int(max_chars or settings.brain_memory_compact_max_chars))
    new_summary = _merge_summary(previous.get("summary_text", "") if previous else "", old_rows, max_chars=limit)
    compacted_until_id = max(int(row["id"]) for row in old_rows)
    source_count = (previous.get("source_count", 0) if previous else 0) + len(old_rows)
    upsert_conversation_summary(conversation_id, new_summary, compacted_until_id, source_count)
    delete_brain_messages_through_id(conversation_id, compacted_until_id)
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "compacted": True,
        "total_before": total,
        "compacted_count": len(old_rows),
        "kept": keep,
        "summary_chars": len(new_summary),
        "compacted_until_id": compacted_until_id,
    }


def build_reverse_context_pack(
    conversation_id: str,
    recent_limit: int = 12,
    max_chars: int = 6000,
    force_compact: bool = False,
    keep_messages: int | None = None,
) -> dict[str, Any]:
    if not conversation_id:
        return {"ok": False, "error": "conversation_id is required"}

    from assetclaw_matting.db.repos import count_brain_messages, get_conversation_summary, get_recent_brain_messages

    compact_result = None
    if force_compact:
        compact_result = compact_conversation(conversation_id, keep_messages=keep_messages, max_chars=max_chars)

    summary = get_conversation_summary(conversation_id) or {}
    recent = get_recent_brain_messages(conversation_id, limit=max(1, min(int(recent_limit), 30)))
    reverse_recent = list(reversed(recent))
    fragments = []
    if summary.get("summary_text"):
        fragments.append({"kind": "summary", "text": str(summary.get("summary_text") or "")})
    for row in reverse_recent:
        user_text = _clean(row.get("message_text", ""))
        response_text = _clean(row.get("response_text", ""))
        text = "\n".join(part for part in (f"用户：{user_text}" if user_text else "", f"助手：{response_text}" if response_text else "") if part)
        if text:
            fragments.append({"kind": "recent_turn", "id": row.get("id"), "created_at": row.get("created_at"), "text": text})

    packed_parts: list[str] = []
    used_chars = 0
    for fragment in fragments:
        text = str(fragment.get("text") or "").strip()
        if not text:
            continue
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        clipped = text[:remaining]
        packed_parts.append(clipped)
        used_chars += len(clipped)

    return {
        "ok": True,
        "conversation_id": conversation_id,
        "message_count": count_brain_messages(conversation_id),
        "summary": summary,
        "recent": recent,
        "reverse_recent": reverse_recent,
        "packed_text": "\n\n".join(packed_parts),
        "packed_chars": used_chars,
        "max_chars": max_chars,
        "force_compact": bool(force_compact),
        "compact_result": compact_result,
    }


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

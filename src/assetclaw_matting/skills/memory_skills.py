from __future__ import annotations

from typing import Any


def memory_remember(key: str, value: str, scope: str = "global", source: str = "brain") -> dict[str, Any]:
    if not key.strip():
        raise ValueError("memory key is required")
    if not value.strip():
        raise ValueError("memory value is required")
    from assetclaw_matting.db.repos import upsert_memory_note

    upsert_memory_note(scope.strip() or "global", key.strip(), value.strip(), source)
    return {"ok": True, "scope": scope.strip() or "global", "key": key.strip()}


def memory_list(scope: str = "global", limit: int = 20) -> dict[str, Any]:
    from assetclaw_matting.db.repos import list_memory_notes

    return {"ok": True, "scope": scope, "items": list_memory_notes(scope, limit)}


def memory_context_pack(
    conversation_id: str = "test",
    recent_limit: int = 12,
    max_chars: int = 6000,
    force_compact: bool = False,
    keep_messages: int | None = None,
) -> dict[str, Any]:
    from assetclaw_matting.brain.memory_compactor import build_reverse_context_pack

    return build_reverse_context_pack(
        conversation_id=conversation_id,
        recent_limit=recent_limit,
        max_chars=max_chars,
        force_compact=force_compact,
        keep_messages=keep_messages,
    )


def memory_compact(
    conversation_id: str = "test",
    keep_messages: int = 12,
    max_chars: int = 6000,
) -> dict[str, Any]:
    from assetclaw_matting.brain.memory_compactor import compact_conversation

    return compact_conversation(conversation_id=conversation_id, keep_messages=keep_messages, max_chars=max_chars)

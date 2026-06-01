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

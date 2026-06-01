from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/queue")
async def queue() -> dict:
    return {"ok": True, "status": "idle", "queued": 0, "running": 0}


class MemoryNoteRequest(BaseModel):
    scope: str = "global"
    key: str
    value: str
    source: str = "admin"


@router.get("/memory")
async def memory(scope: str = "global", limit: int = 20) -> dict:
    from assetclaw_matting.db.repos import list_memory_notes

    return {"ok": True, "scope": scope, "items": list_memory_notes(scope, limit)}


@router.post("/memory")
async def save_memory(body: MemoryNoteRequest) -> dict:
    from assetclaw_matting.db.repos import upsert_memory_note

    upsert_memory_note(body.scope, body.key, body.value, body.source)
    return {"ok": True}


@router.get("/brain-messages")
async def brain_messages(conversation_id: str = "test", limit: int = 20) -> dict:
    from assetclaw_matting.db.repos import get_recent_brain_messages

    return {
        "ok": True,
        "conversation_id": conversation_id,
        "items": get_recent_brain_messages(conversation_id, limit),
    }

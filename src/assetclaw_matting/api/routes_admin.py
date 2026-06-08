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


@router.get("/skill-calls")
async def skill_calls(limit: int = 50, ok: int | None = None, skill: str = "") -> dict:
    from assetclaw_matting.db.sqlite import get_connection

    clauses = []
    values: list[object] = []
    if ok is not None:
        clauses.append("ok = ?")
        values.append(1 if ok else 0)
    if skill:
        clauses.append("skill LIKE ?")
        values.append(f"%{skill}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(max(1, min(limit, 200)))
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, skill, arguments_json, result_json, ok, error, requested_by, created_at
            FROM skill_calls
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
    return {"ok": True, "items": [dict(row) for row in rows]}

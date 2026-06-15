from __future__ import annotations

import shutil
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])
_admin_executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="admin_api")
_admin_cache_lock = threading.Lock()
_admin_cache: dict[str, tuple[float, dict]] = {}
_admin_locks: dict[str, asyncio.Lock] = {}
_ADMIN_CACHE_TTL_SECONDS = 3.0


async def _run_admin(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_admin_executor, lambda: fn(*args))


def _cached(key: str) -> dict | None:
    now = time.monotonic()
    with _admin_cache_lock:
        item = _admin_cache.get(key)
        if not item:
            return None
        expires_at, payload = item
        if expires_at < now:
            _admin_cache.pop(key, None)
            return None
        cached = dict(payload)
        cached["cached"] = True
        return cached


def _store_cache(key: str, payload: dict) -> dict:
    with _admin_cache_lock:
        _admin_cache[key] = (time.monotonic() + _ADMIN_CACHE_TTL_SECONDS, dict(payload))
    return payload


def _clear_cache() -> None:
    with _admin_cache_lock:
        _admin_cache.clear()


def _singleflight_lock(key: str) -> asyncio.Lock:
    with _admin_cache_lock:
        lock = _admin_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _admin_locks[key] = lock
        return lock


async def _cached_or_load(key: str, loader) -> dict:
    cached = _cached(key)
    if cached:
        return cached
    async with _singleflight_lock(key):
        cached = _cached(key)
        if cached:
            return cached
        return _store_cache(key, await _run_admin(loader))


@router.get("/queue")
async def queue() -> dict:
    from assetclaw_matting.services.agent_job_queue import queue_snapshot

    key = "queue"
    return await _cached_or_load(key, queue_snapshot)


class MemoryNoteRequest(BaseModel):
    scope: str = "global"
    key: str
    value: str
    source: str = "admin"


class CleanupRequest(BaseModel):
    target: str
    conversation_id: str = "test"
    scope: str = "global"


@router.get("/memory")
async def memory(scope: str = "global", limit: int = 20) -> dict:
    from assetclaw_matting.db.repos import list_memory_notes

    key = f"memory:{scope}:{limit}"
    return await _cached_or_load(key, lambda: {"ok": True, "scope": scope, "items": list_memory_notes(scope, limit)})


@router.get("/memory-pack")
async def memory_pack(scope: str = "global", conversation_id: str = "test", limit: int = 10) -> dict:
    from assetclaw_matting.brain.memory_compactor import build_reverse_context_pack
    from assetclaw_matting.db.repos import list_memory_notes

    def _load() -> dict:
        return {
            "ok": True,
            "scope": scope,
            "conversation_id": conversation_id,
            "items": list_memory_notes(scope, limit),
            "pack": build_reverse_context_pack(conversation_id=conversation_id, recent_limit=6, max_chars=3000),
        }

    key = f"memory-pack:{scope}:{conversation_id}:{limit}"
    return await _cached_or_load(key, _load)


@router.post("/memory")
async def save_memory(body: MemoryNoteRequest) -> dict:
    from assetclaw_matting.db.repos import upsert_memory_note

    await _run_admin(upsert_memory_note, body.scope, body.key, body.value, body.source)
    _clear_cache()
    return {
        "ok": True,
    }


@router.get("/brain-messages")
async def brain_messages(conversation_id: str = "test", limit: int = 20) -> dict:
    from assetclaw_matting.db.repos import get_recent_brain_messages

    key = f"brain-messages:{conversation_id}:{limit}"
    return await _cached_or_load(key, lambda: {
        "ok": True,
        "conversation_id": conversation_id,
        "items": get_recent_brain_messages(conversation_id, limit),
    })


@router.get("/skill-calls")
async def skill_calls(limit: int = 50, ok: int | None = None, skill: str = "") -> dict:
    from assetclaw_matting.db.sqlite import get_connection

    def _load() -> list[dict]:
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
        return [dict(row) for row in rows]

    key = f"skill-calls:{limit}:{ok}:{skill}"
    return await _cached_or_load(key, lambda: {"ok": True, "items": _load()})


@router.post("/cleanup")
async def cleanup(body: CleanupRequest) -> dict:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    def _clean() -> tuple[bool, str, int]:
        target = body.target.strip().lower()
        deleted = 0
        with get_connection() as conn:
            if target == "brain_messages":
                cursor = conn.execute("DELETE FROM brain_messages WHERE conversation_id = ?", (body.conversation_id or "test",))
                deleted = cursor.rowcount
            elif target == "all_brain_messages":
                cursor = conn.execute("DELETE FROM brain_messages")
                deleted = cursor.rowcount
                conn.execute("DELETE FROM conversation_summaries")
            elif target == "skill_calls":
                cursor = conn.execute("DELETE FROM skill_calls")
                deleted = cursor.rowcount
            elif target == "memory":
                cursor = conn.execute("DELETE FROM memory_notes WHERE scope = ?", (body.scope or "global",))
                deleted = cursor.rowcount
            elif target == "all_memory":
                cursor = conn.execute("DELETE FROM memory_notes")
                deleted = cursor.rowcount
                conn.execute("DELETE FROM conversation_summaries")
            elif target == "production_runs":
                tables = ["comfyui_runs", "cherry_runs", "frame_runs", "pipeline_runs", "shared_matting_runs", "task_logs", "tasks", "batches"]
                for table in tables:
                    cursor = conn.execute(f"DELETE FROM {table}")
                    deleted += max(0, cursor.rowcount)
            elif target == "feishu_dedup":
                cursor = conn.execute("DELETE FROM feishu_event_dedup")
                deleted = cursor.rowcount
            elif target == "pending_confirmations":
                cursor = conn.execute("DELETE FROM pending_confirmations")
                deleted = cursor.rowcount
            elif target == "agent_jobs":
                from assetclaw_matting.services.agent_job_queue import clear_pending_brain_jobs

                deleted += clear_pending_brain_jobs()
                deleted += _remove_dir(Path(settings.storage_dir) / "agent_jobs")
            elif target == "stress_agent_jobs":
                from assetclaw_matting.services.agent_job_queue import clear_pending_brain_jobs

                deleted += clear_pending_brain_jobs("webui_proxy_stress_")
                deleted += _remove_dir(Path(settings.storage_dir) / "agent_jobs")
            elif target == "custom_pipeline_runs":
                deleted = _remove_dir(Path(settings.storage_dir) / "custom_pipeline_runs")
            else:
                return False, target, 0
        return True, target, deleted

    ok, target, deleted = await _run_admin(_clean)
    _clear_cache()
    if not ok:
        return {"ok": False, "error": f"unknown cleanup target: {body.target}"}
    return {"ok": True, "target": target, "deleted": deleted}


def _remove_dir(path: Path) -> int:
    if not path.exists():
        return 0
    count = sum(1 for item in path.rglob("*") if item.is_file())
    shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return count

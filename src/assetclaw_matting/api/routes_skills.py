from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/skills/v1", tags=["skills"])
_skill_executor: ThreadPoolExecutor | None = None
_cache_lock = threading.Lock()
_skill_cache: dict[str, tuple[float, dict]] = {}
_skill_locks: dict[str, asyncio.Lock] = {}
_CACHE_TTL_SECONDS = 5.0


class SkillCallRequest(BaseModel):
    skill: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = "api"


def _verify(x_skill_token: str = Header("")) -> None:
    from assetclaw_matting.config import settings

    if x_skill_token != settings.skill_api_token:
        raise HTTPException(status_code=401, detail="invalid X-Skill-Token")


async def _run_skill_thread(fn, *args):
    global _skill_executor
    from assetclaw_matting.config import settings

    if _skill_executor is None:
        _skill_executor = ThreadPoolExecutor(max_workers=max(4, int(settings.skill_threadpool_workers or 16)), thread_name_prefix="skill_api")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_skill_executor, lambda: fn(*args))


def _cache_key(skill: str, args: dict[str, Any]) -> str:
    return f"{skill}:{json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)}"


def _cacheable(skill: str, requested_by: str = "") -> bool:
    if requested_by == "brain":
        return False
    readonly_prefixes = (
        "queue.",
        "system.",
        "memory.list",
        "custom_pipeline.module_catalog",
        "custom_pipeline.list_definitions",
        "custom_pipeline.run_list",
        "custom_pipeline.run_status",
        "animation_flow.list",
        "animation_flow.status",
        "comfyui.queue_status",
        "comfyui.run_list",
        "comfyui.run_status",
        "cherry.run_list",
        "cherry.run_status",
        "frame.run_list",
        "frame.run_status",
        "pipeline.run_list",
        "pipeline.run_status",
        "unity_ready.preview",
        "unity_ready.status",
        "unity_import.preview",
        "unity_import.status",
        "animation.status",
        "agent.current_work",
    )
    return skill.startswith(readonly_prefixes)


def _get_cached(key: str) -> dict | None:
    now = time.monotonic()
    with _cache_lock:
        item = _skill_cache.get(key)
        if not item:
            return None
        expires_at, payload = item
        if expires_at < now:
            _skill_cache.pop(key, None)
            return None
        cached = dict(payload)
        cached["cached"] = True
        return cached


def _set_cached(key: str, payload: dict) -> None:
    with _cache_lock:
        _skill_cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, dict(payload))


def _singleflight_lock(key: str) -> asyncio.Lock:
    with _cache_lock:
        lock = _skill_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _skill_locks[key] = lock
        return lock


@router.get("/manifest")
async def manifest() -> dict:
    from assetclaw_matting.skills.registry import get_manifest

    key = "manifest"
    cached = _get_cached(key)
    if cached:
        return cached
    async with _singleflight_lock(key):
        cached = _get_cached(key)
        if cached:
            return cached
        payload = await _run_skill_thread(get_manifest)
        _set_cached(key, payload)
        return payload


@router.post("/call")
async def call(body: SkillCallRequest, x_skill_token: str = Header("")) -> dict:
    _verify(x_skill_token)
    from assetclaw_matting.skills.registry import call_skill

    key = _cache_key(body.skill, body.arguments)
    if _cacheable(body.skill, body.requested_by):
        cached = _get_cached(key)
        if cached:
            return cached
        async with _singleflight_lock(key):
            cached = _get_cached(key)
            if cached:
                return cached
            payload = await _run_skill_thread(call_skill, body.skill, body.arguments, body.requested_by)
            if payload.get("ok") is not False:
                _set_cached(key, payload)
            return payload
    return await _run_skill_thread(call_skill, body.skill, body.arguments, body.requested_by)

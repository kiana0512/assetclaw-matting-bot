from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from assetclaw_matting.config import settings


PROFILE_CACHE_PATH = Path(settings.storage_dir) / "feishu_user_profiles.json"
SUCCESS_TTL_SECONDS = 24 * 60 * 60
FAILURE_TTL_SECONDS = 6 * 60 * 60
_LOCK = threading.Lock()
_IN_FLIGHT: set[str] = set()
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOADED = False


def remember_user_profile(user_id: str, display_name: str, avatar_url: str = "") -> dict[str, str]:
    user_id = str(user_id or "").strip()
    display_name = str(display_name or "").strip()
    if not user_id or not display_name:
        return _fallback()
    with _LOCK:
        cache = _read_cache()
        cache[user_id] = {
            "display_name": display_name,
            "avatar_url": str(avatar_url or "").strip(),
            "updated_at": time.time(),
            "retry_after": 0,
        }
        _write_cache(cache)
    return _public(cache[user_id])


def get_cached_user_profile(user_id: str) -> dict[str, str]:
    user_id = str(user_id or "").strip()
    if not user_id:
        return _fallback("本地任务")
    with _LOCK:
        item = _read_cache().get(user_id) or {}
    return _public(item) if item.get("display_name") else _fallback()


def profile_for_run(run: dict[str, Any]) -> dict[str, str]:
    persisted = run.get("feishu_user") or {}
    if str(persisted.get("display_name") or "").strip():
        return _public(persisted)
    user_id = str(run.get("user_id") or "").strip()
    profile = get_cached_user_profile(user_id)
    if user_id:
        schedule_user_profile_refresh(user_id)
    return profile


def schedule_user_profile_refresh(user_id: str) -> None:
    user_id = str(user_id or "").strip()
    if not user_id:
        return
    with _LOCK:
        item = _read_cache().get(user_id) or {}
        now = time.time()
        if user_id in _IN_FLIGHT:
            return
        if item.get("display_name") and now - float(item.get("updated_at") or 0) < SUCCESS_TTL_SECONDS:
            return
        if float(item.get("retry_after") or 0) > now:
            return
        _IN_FLIGHT.add(user_id)
    threading.Thread(target=_refresh, args=(user_id,), daemon=True, name=f"feishu-profile-{user_id[-6:]}").start()


def _refresh(user_id: str) -> None:
    try:
        from assetclaw_matting.feishu.client import feishu_client

        profile = feishu_client.get_user_profile(user_id)
        name = str(profile.get("display_name") or "").strip()
        if not name:
            raise RuntimeError("Feishu profile has no display name")
        remember_user_profile(user_id, name, str(profile.get("avatar_url") or ""))
    except Exception as exc:
        with _LOCK:
            cache = _read_cache()
            current = cache.get(user_id) or {}
            current.update({
                "retry_after": time.time() + FAILURE_TTL_SECONDS,
                "last_error": str(exc),
            })
            cache[user_id] = current
            _write_cache(cache)
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(user_id)


def _public(item: dict[str, Any]) -> dict[str, str]:
    return {
        "display_name": str(item.get("display_name") or "飞书用户").strip(),
        "avatar_url": str(item.get("avatar_url") or "").strip(),
        "source": "feishu",
    }


def _fallback(name: str = "飞书用户") -> dict[str, str]:
    return {"display_name": name, "avatar_url": "", "source": "feishu"}


def _read_cache() -> dict[str, dict[str, Any]]:
    global _CACHE_LOADED
    if _CACHE_LOADED:
        return _CACHE
    try:
        payload = json.loads(PROFILE_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            _CACHE.clear()
            _CACHE.update(payload)
    except (OSError, ValueError, TypeError):
        _CACHE.clear()
    _CACHE_LOADED = True
    return _CACHE


def _write_cache(cache: dict[str, dict[str, Any]]) -> None:
    global _CACHE_LOADED
    if cache is not _CACHE:
        _CACHE.clear()
        _CACHE.update(cache)
    _CACHE_LOADED = True
    PROFILE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    partial = PROFILE_CACHE_PATH.with_suffix(".json.tmp")
    partial.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(PROFILE_CACHE_PATH)

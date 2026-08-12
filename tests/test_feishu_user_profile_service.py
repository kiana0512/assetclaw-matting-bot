from __future__ import annotations

import json
from pathlib import Path


def test_profile_cache_uses_memory_after_initial_disk_read(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.services import feishu_user_profile_service as service

    cache_path = tmp_path / "profiles.json"
    cache_path.write_text(
        json.dumps({"ou_test": {"display_name": "测试用户", "avatar_url": "avatar"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "PROFILE_CACHE_PATH", cache_path)
    monkeypatch.setattr(service, "_CACHE_LOADED", False)
    service._CACHE.clear()

    assert service.get_cached_user_profile("ou_test")["display_name"] == "测试用户"
    cache_path.unlink()
    assert service.get_cached_user_profile("ou_test")["avatar_url"] == "avatar"


def test_profile_fallback_never_exposes_open_id(monkeypatch, tmp_path: Path) -> None:
    from assetclaw_matting.services import feishu_user_profile_service as service

    monkeypatch.setattr(service, "PROFILE_CACHE_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(service, "_CACHE_LOADED", False)
    service._CACHE.clear()

    profile = service.get_cached_user_profile("ou_secret_internal_id")

    assert profile["display_name"] == "飞书用户"
    assert "ou_secret" not in profile["display_name"]

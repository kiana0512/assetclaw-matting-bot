"""Tests for path security: allowed roots, deny patterns, traversal."""
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture()
def restricted_settings(tmp_path, monkeypatch):
    import assetclaw_matting.config as cfg_module
    import assetclaw_matting.services.file_store as fs_module
    from assetclaw_matting.config import Settings

    allowed = tmp_path / "workspace"
    allowed.mkdir()

    s = Settings(
        storage_dir=str(tmp_path / "storage"),
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        allowed_roots=str(allowed),
        deny_path_patterns=".ssh;.env;Windows;Program Files",
    )
    monkeypatch.setattr(cfg_module, "settings", s)
    monkeypatch.setattr(fs_module, "settings", s)
    s.ensure_dirs()
    return s, allowed


# ── file_store.validate_allowed_path ─────────────────────────────────────────

def test_path_inside_allowed_root_passes(restricted_settings):
    from assetclaw_matting.services.file_store import validate_allowed_path
    s, allowed = restricted_settings
    subdir = allowed / "batch_inputs"
    subdir.mkdir()
    validate_allowed_path(subdir)  # should not raise


def test_path_outside_allowed_root_fails(restricted_settings, tmp_path):
    from assetclaw_matting.services.file_store import validate_allowed_path
    s, allowed = restricted_settings
    forbidden = tmp_path / "secret"
    forbidden.mkdir()
    with pytest.raises(ValueError, match="not under any allowed root"):
        validate_allowed_path(forbidden)


def test_empty_allowed_roots_allows_anything(tmp_path, monkeypatch):
    import assetclaw_matting.config as cfg_module
    import assetclaw_matting.services.file_store as fs_module
    from assetclaw_matting.config import Settings

    s = Settings(
        storage_dir=str(tmp_path / "storage"),
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        allowed_roots="",
    )
    monkeypatch.setattr(cfg_module, "settings", s)
    monkeypatch.setattr(fs_module, "settings", s)

    from assetclaw_matting.services.file_store import validate_allowed_path
    validate_allowed_path(tmp_path)  # should not raise


# ── skills/auth.validate_skill_path ──────────────────────────────────────────

def test_skill_path_deny_env_pattern(restricted_settings, tmp_path):
    from assetclaw_matting.skills.auth import validate_skill_path
    s, allowed = restricted_settings

    # Path containing ".env" fragment
    bad = allowed / ".env_data"
    bad.mkdir()
    with pytest.raises(ValueError, match="denied pattern"):
        validate_skill_path(str(bad))


def test_skill_path_deny_windows_pattern(restricted_settings, tmp_path):
    """Windows system directory should be denied."""
    from assetclaw_matting.skills.auth import validate_skill_path
    s, allowed = restricted_settings

    import assetclaw_matting.skills.auth as auth_mod
    import assetclaw_matting.config as cfg_mod

    # Manually check pattern matching by constructing a path with "Windows" in it
    bad_path = allowed / "Windows" / "System32"
    bad_path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="denied pattern"):
        validate_skill_path(str(bad_path))


def test_skill_path_traversal_rejected(restricted_settings):
    from assetclaw_matting.skills.auth import validate_skill_path
    s, allowed = restricted_settings
    with pytest.raises(ValueError, match="traversal"):
        validate_skill_path(str(allowed / ".." / ".." / "etc" / "passwd"))


def test_skill_path_valid(restricted_settings):
    from assetclaw_matting.skills.auth import validate_skill_path
    s, allowed = restricted_settings
    subdir = allowed / "inputs"
    subdir.mkdir()
    result = validate_skill_path(str(subdir))
    assert result == subdir.resolve()

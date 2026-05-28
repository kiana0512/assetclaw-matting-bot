"""Tests for skills/auth.py: token validation and path security."""
from __future__ import annotations

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_config(monkeypatch, tmp_path):
    import assetclaw_matting.config as cfg_module
    import assetclaw_matting.skills.auth as auth_module
    from assetclaw_matting.config import Settings

    allowed = tmp_path / "allowed"
    allowed.mkdir()

    s = Settings(
        storage_dir=str(tmp_path / "storage"),
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        skill_api_token="test_secret",
        skill_api_enabled=True,
        allowed_roots=str(allowed),
        deny_path_patterns=".ssh;.env;Windows",
    )
    monkeypatch.setattr(cfg_module, "settings", s)
    monkeypatch.setattr(auth_module, "settings", s, raising=False)
    s.ensure_dirs()
    yield s, allowed


# ── Token validation ──────────────────────────────────────────────────────────

def test_verify_skill_token_correct(patch_config):
    from assetclaw_matting.skills.auth import verify_skill_token
    # Should not raise
    verify_skill_token(x_skill_token="test_secret")


def test_verify_skill_token_wrong(patch_config):
    from fastapi import HTTPException
    from assetclaw_matting.skills.auth import verify_skill_token
    with pytest.raises(HTTPException) as exc_info:
        verify_skill_token(x_skill_token="wrong_token")
    assert exc_info.value.status_code == 401


def test_verify_skill_token_disabled(monkeypatch, patch_config):
    import assetclaw_matting.config as cfg_module
    import assetclaw_matting.skills.auth as auth_module
    from assetclaw_matting.config import Settings
    from fastapi import HTTPException

    s = Settings(skill_api_enabled=False, skill_api_token="test_secret")
    monkeypatch.setattr(cfg_module, "settings", s)
    monkeypatch.setattr(auth_module, "settings", s, raising=False)

    from assetclaw_matting.skills.auth import verify_skill_token
    with pytest.raises(HTTPException) as exc_info:
        verify_skill_token(x_skill_token="test_secret")
    assert exc_info.value.status_code == 503


# ── Path security ─────────────────────────────────────────────────────────────

def test_allowed_path_passes(patch_config, tmp_path):
    s, allowed = patch_config
    import assetclaw_matting.services.file_store as fs_module
    import assetclaw_matting.config as cfg_module

    # Patch file_store settings too
    import importlib
    import assetclaw_matting.services.file_store as fs
    fs.settings = s
    cfg_module.settings = s

    from assetclaw_matting.skills.auth import validate_skill_path
    result = validate_skill_path(str(allowed))
    assert result.exists()


def test_path_traversal_rejected(patch_config):
    from assetclaw_matting.skills.auth import validate_skill_path
    with pytest.raises(ValueError, match="traversal"):
        validate_skill_path("E:\\allowed\\..\\..\\etc\\passwd")


def test_deny_pattern_rejected(patch_config, tmp_path):
    """Path containing a deny pattern should be rejected."""
    from assetclaw_matting.skills.auth import validate_skill_path

    s, allowed = patch_config
    # Create a path that matches deny pattern (.env substring)
    forbidden = tmp_path / "allowed" / ".env_secrets"
    forbidden.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="denied pattern"):
        validate_skill_path(str(forbidden))


def test_validate_log_name_allowed(patch_config):
    from assetclaw_matting.skills.auth import validate_log_name
    assert validate_log_name("gateway") == "gateway"
    assert validate_log_name("worker.log") == "worker"
    assert validate_log_name("APP") == "app"


def test_validate_log_name_rejected(patch_config):
    from assetclaw_matting.skills.auth import validate_log_name
    with pytest.raises(ValueError, match="Unknown log name"):
        validate_log_name("secret_tokens")

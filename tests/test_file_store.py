"""Tests for services/file_store.py"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def patch_settings(tmp_path, monkeypatch):
    import assetclaw_matting.config as cfg_module
    from assetclaw_matting.config import Settings

    new_settings = Settings(
        storage_dir=str(tmp_path / "storage"),
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        allowed_roots="",
    )
    monkeypatch.setattr(cfg_module, "settings", new_settings)

    import assetclaw_matting.services.file_store as fs_module
    monkeypatch.setattr(fs_module, "settings", new_settings)
    new_settings.ensure_dirs()
    yield new_settings


def test_create_task_meta_dir(patch_settings):
    from assetclaw_matting.services.file_store import create_task_meta_dir

    d = create_task_meta_dir("abc-123")
    assert d.is_dir()
    assert d.name == "abc-123"


def test_scan_images(tmp_path):
    from assetclaw_matting.services.file_store import scan_images
    from PIL import Image

    d = tmp_path / "imgs"
    d.mkdir()
    Image.new("RGB", (8, 8)).save(str(d / "a.png"))
    Image.new("RGB", (8, 8)).save(str(d / "b.jpg"))
    (d / "c.txt").write_text("ignore me")

    images = scan_images(d)
    assert len(images) == 2
    assert all(f.suffix in (".png", ".jpg") for f in images)


def test_compute_output_path(tmp_path):
    from assetclaw_matting.services.file_store import compute_output_path

    out_dir = tmp_path / "out"
    inp = tmp_path / "foo.png"
    result = compute_output_path(out_dir, inp)
    assert result.name == "foo_matting.png"
    assert result.parent == out_dir


def test_path_escape_rejected(patch_settings):
    from assetclaw_matting.services.file_store import _safe_path

    base = patch_settings.tasks_dir
    with pytest.raises(ValueError, match="Path escape"):
        _safe_path(base, "../../../etc/passwd")


def test_allowed_roots_validation(tmp_path, monkeypatch):
    import assetclaw_matting.config as cfg_module
    import assetclaw_matting.services.file_store as fs_module
    from assetclaw_matting.config import Settings

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()

    new_settings = Settings(
        storage_dir=str(tmp_path / "storage"),
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        allowed_roots=str(allowed),
    )
    monkeypatch.setattr(cfg_module, "settings", new_settings)
    monkeypatch.setattr(fs_module, "settings", new_settings)

    from assetclaw_matting.services.file_store import validate_allowed_path

    # Allowed path should not raise
    validate_allowed_path(allowed / "subdir")

    # Forbidden path should raise
    with pytest.raises(ValueError, match="not under any allowed root"):
        validate_allowed_path(forbidden)

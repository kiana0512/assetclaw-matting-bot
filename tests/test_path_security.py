from __future__ import annotations

from pathlib import Path

import pytest

from assetclaw_matting.config import Settings, settings
from assetclaw_matting.skills.security import validate_path


def test_project_drive_allowed() -> None:
    root = settings.allowed_roots_list[0]
    assert validate_path(root).is_absolute()


def test_animation_root_allowed() -> None:
    assert validate_path(settings.animation_root).is_absolute()


def test_repo_allowed() -> None:
    assert validate_path(settings.assetclaw_root) == settings.assetclaw_root.resolve()


def test_settings_follow_a_relocated_checkout(tmp_path: Path) -> None:
    project = tmp_path / "assetclaw-matting-bot"
    relocated = Settings(assetclaw_root=project)

    assert relocated.assetclaw_root == project.resolve()
    assert relocated.animation_root == project.resolve().parent / "animation_auto"
    assert relocated.storage_dir == project.resolve() / "storage"
    assert relocated.allowed_roots_list == [project.resolve().anchor]


@pytest.mark.parametrize(
    "path",
    [
        str(settings.assetclaw_root / ".env"),
        str(Path(settings.assetclaw_root.anchor) / "Windows"),
        str(Path(settings.assetclaw_root.anchor) / "$Recycle.Bin"),
        str(settings.assetclaw_root / ".." / "Windows"),
    ],
)
def test_denied_paths(path: str) -> None:
    with pytest.raises((PermissionError, ValueError)):
        validate_path(path)

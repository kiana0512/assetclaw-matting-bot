from __future__ import annotations

import pytest

from assetclaw_matting.skills.security import validate_path


def test_e_drive_allowed() -> None:
    assert str(validate_path("E:\\")).startswith("E:")


@pytest.mark.parametrize("path", ["D:\\", "F:\\"])
def test_work_drives_allowed(path: str) -> None:
    assert str(validate_path(path)).startswith(path[:2])


def test_c_drive_denied() -> None:
    with pytest.raises(PermissionError):
        validate_path("C:\\")


def test_repo_allowed() -> None:
    assert str(validate_path("E:\\assetclaw-matting-bot")).startswith("E:")


@pytest.mark.parametrize(
    "path",
    [
        "E:\\assetclaw-matting-bot\\.env",
        "E:\\Windows",
        "E:\\$Recycle.Bin",
        "E:\\assetclaw-matting-bot\\..\\Windows",
    ],
)
def test_denied_paths(path: str) -> None:
    with pytest.raises((PermissionError, ValueError)):
        validate_path(path)

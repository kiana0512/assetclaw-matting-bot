from __future__ import annotations

from pathlib import Path

from assetclaw_matting.skills.file_skills import file_copy, file_exists, file_list_allowed, file_mkdir, file_move


def test_list_allowed() -> None:
    result = file_list_allowed("E:\\assetclaw-matting-bot", max_items=10)
    assert result["ok"] is True
    assert isinstance(result["items"], list)


def test_list_allowed_filters_denied_root_entries() -> None:
    result = file_list_allowed("E:\\", max_items=100)
    names = {item["name"] for item in result["items"]}
    assert "$RECYCLE.BIN" not in names
    assert "System Volume Information" not in names


def test_copy_file() -> None:
    dst = ".\\storage\\debug\\pytest_copy.md"
    result = file_copy(".\\README.md", dst, overwrite=True)
    assert result["ok"] is True
    assert Path(dst).exists()


def test_copy_existing_without_overwrite() -> None:
    dst = ".\\storage\\debug\\pytest_exists.md"
    file_copy(".\\README.md", dst, overwrite=True)
    try:
        file_copy(".\\README.md", dst, overwrite=False)
    except Exception:
        return
    raise AssertionError("expected destination exists error")


def test_copy_env_denied() -> None:
    try:
        file_copy(".\\.env", ".\\storage\\debug\\env.copy")
    except Exception:
        return
    raise AssertionError("expected .env denial")


def test_mkdir_and_exists() -> None:
    path = ".\\storage\\debug\\pytest_dir"
    made = file_mkdir(path)
    assert made["ok"] is True
    exists = file_exists(path)
    assert exists["exists"] is True
    assert exists["is_dir"] is True


def test_move_file() -> None:
    src = ".\\storage\\debug\\pytest_move_src.md"
    dst = ".\\storage\\debug\\pytest_move_dst.md"
    file_copy(".\\README.md", src, overwrite=True)
    moved = file_move(src, dst, overwrite=True)
    assert moved["ok"] is True
    assert Path(dst).exists()
    assert not Path(src).exists()

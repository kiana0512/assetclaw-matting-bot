from __future__ import annotations

from pathlib import Path

from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.config import settings
from assetclaw_matting.skills.registry import get_skill_meta
from assetclaw_matting.skills.workspace_skills import (
    file_append_text,
    file_batch_info,
    file_copy_many,
    file_copy_tree,
    file_hash,
    file_mkdir_many,
    file_move_many,
    file_read_text,
    file_rename_many,
    file_rename_sequence,
    file_unzip,
    file_write_text,
    workspace_roots,
)


def setup_module() -> None:
    init_db(Path.cwd() / "data/test_assetclaw.db")
    create_tables()


def test_workspace_roots_follow_config_and_exclude_windows() -> None:
    roots = workspace_roots()["roots"]
    root_text = ";".join(item["path"] for item in roots)
    assert settings.allowed_roots_list[0] in root_text
    assert "Windows" not in root_text


def test_text_write_read_append_hash_and_batch_info() -> None:
    path = ".\\storage\\debug\\workspace_note.txt"
    written = file_write_text(path, "hello", overwrite=True)
    assert written["ok"] is True
    appended = file_append_text(path, "\nworld")
    assert appended["ok"] is True
    read = file_read_text(path)
    assert "hello" in read["text"]
    digest = file_hash(path)
    assert digest["algorithm"] == "sha256"
    assert len(digest["hash"]) == 64
    batch = file_batch_info([path, ".\\storage\\debug\\missing.txt"])
    assert batch["count"] == 2


def test_copy_tree_and_dangerous_skills_require_confirmation() -> None:
    src = Path.cwd() / "storage/debug/tree_src"
    dst = Path.cwd() / "storage/debug/tree_dst"
    src.mkdir(parents=True, exist_ok=True)
    (src / "a.txt").write_text("a", encoding="utf-8")
    copied = file_copy_tree(str(src), str(dst), overwrite=True)
    assert copied["ok"] is True
    assert (dst / "a.txt").exists()
    assert get_skill_meta("file.delete")["requires_confirmation"] is True
    assert get_skill_meta("file.empty_dir")["requires_confirmation"] is True
    assert get_skill_meta("file.move")["requires_confirmation"] is True
    assert get_skill_meta("file.rename_sequence")["requires_confirmation"] is True


def test_rename_sequence_preserves_extensions() -> None:
    root = Path.cwd() / "storage/debug/rename_sequence"
    root.mkdir(parents=True, exist_ok=True)
    a = root / "first.png"
    b = root / "second.jpg"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    for target in (root / "1.png", root / "2.jpg"):
        if target.exists():
            target.unlink()

    result = file_rename_sequence([str(a), str(b)])
    assert result["ok"] is True
    assert (root / "1.png").exists()
    assert (root / "2.jpg").exists()


def test_batch_file_operations_and_unzip() -> None:
    import zipfile

    root = Path.cwd() / "storage/debug/batch_ops"
    src_dir = root / "src"
    dst_dir = root / "dst"
    moved_dir = root / "moved"
    zip_out = root / "archive.zip"
    unzip_dir = root / "unzipped"
    src_dir.mkdir(parents=True, exist_ok=True)
    dst_dir.mkdir(parents=True, exist_ok=True)
    moved_dir.mkdir(parents=True, exist_ok=True)
    a = src_dir / "a.txt"
    b = src_dir / "b.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    made = file_mkdir_many([str(root / "one"), str(root / "two")])
    assert made["count"] == 2

    copied = file_copy_many([
        {"src_path": str(a), "dst_path": str(dst_dir / "a.txt")},
        {"src_path": str(b), "dst_path": str(dst_dir / "b.txt")},
    ], overwrite=True)
    assert copied["count"] == 2

    renamed = file_rename_many([
        {"src_path": str(dst_dir / "a.txt"), "dst_path": str(dst_dir / "renamed_a.txt")},
    ], overwrite=True)
    assert renamed["count"] == 1
    assert (dst_dir / "renamed_a.txt").exists()

    moved = file_move_many([
        {"src_path": str(dst_dir / "b.txt"), "dst_path": str(moved_dir / "b.txt")},
    ], overwrite=True)
    assert moved["count"] == 1
    assert (moved_dir / "b.txt").exists()

    with zipfile.ZipFile(zip_out, "w") as zf:
        zf.write(a, arcname="a.txt")
    unzipped = file_unzip(str(zip_out), str(unzip_dir), overwrite=True)
    assert unzipped["count"] == 1
    assert (unzip_dir / "a.txt").exists()

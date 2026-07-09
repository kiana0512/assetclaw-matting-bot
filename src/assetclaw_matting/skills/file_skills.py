from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.security import has_denied_pattern, validate_path


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def file_list_allowed(path: str, max_items: int | None = None) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    if not settings.allow_file_list:
        raise PermissionError("file.list_allowed is disabled")
    target = validate_path(path, must_exist=True)
    if not target.is_dir():
        raise ValueError("path must be a directory")
    limit = min(int(max_items or settings.max_list_items), settings.max_list_items)
    items: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if has_denied_pattern(child):
            continue
        try:
            stat = child.stat()
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": None if child.is_dir() else stat.st_size,
                    "modified_at": _iso_mtime(child),
                }
            )
        except OSError:
            continue
        if len(items) >= limit:
            break
    return {"ok": True, "path": str(target), "items": items}


def file_copy(src_path: str, dst_path: str, overwrite: bool = False) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    if not settings.allow_file_copy:
        raise PermissionError("file.copy is disabled")
    src = validate_path(src_path, must_exist=True)
    dst = validate_path(dst_path, must_exist=False)
    if src.is_dir():
        raise ValueError("file.copy only supports files")
    if dst.exists() and not overwrite:
        raise FileExistsError("destination already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"ok": True, "src_path": str(src), "dst_path": str(dst), "size": dst.stat().st_size}


def file_move(src_path: str, dst_path: str, overwrite: bool = False) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    if not settings.allow_file_move:
        raise PermissionError("file.move is disabled")
    src = validate_path(src_path, must_exist=True)
    dst = validate_path(dst_path, must_exist=False)
    if dst.exists() and not overwrite:
        raise FileExistsError("destination already exists")
    if dst.exists() and overwrite:
        if dst.is_dir():
            raise ValueError("cannot overwrite an existing directory")
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    moved = shutil.move(str(src), str(dst))
    target = Path(moved)
    return {
        "ok": True,
        "src_path": str(src),
        "dst_path": str(target),
        "is_dir": target.is_dir(),
        "size": None if target.is_dir() else target.stat().st_size,
    }


def file_mkdir(path: str, parents: bool = True, exist_ok: bool = True) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    if not settings.allow_file_mkdir:
        raise PermissionError("file.mkdir is disabled")
    target = validate_path(path, must_exist=False)
    target.mkdir(parents=parents, exist_ok=exist_ok)
    return {"ok": True, "path": str(target), "exists": target.exists(), "is_dir": target.is_dir()}


def file_exists(path: str) -> dict[str, Any]:
    target = validate_path(path, must_exist=False)
    exists = target.exists()
    return {
        "ok": True,
        "path": str(target),
        "exists": exists,
        "is_dir": target.is_dir() if exists else False,
        "is_file": target.is_file() if exists else False,
        "size": target.stat().st_size if exists and target.is_file() else None,
    }

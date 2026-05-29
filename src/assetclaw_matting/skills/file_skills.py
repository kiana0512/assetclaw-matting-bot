"""Skill implementations for controlled file system access.

Security constraints:
- Only paths under ALLOWED_ROOTS are accessible.
- DENY_PATH_PATTERNS are always blocked.
- File content is never returned.
- File deletion is never permitted via skills.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.auth import validate_skill_path


def file_list_allowed(
    path: str,
    max_items: int = 100,
) -> dict[str, Any]:
    """List files and directories under an allowed path.

    Returns file metadata only (name, size, mtime, is_dir).
    Never returns file content.
    """
    from assetclaw_matting.config import settings
    if not settings.allow_file_list:
        raise PermissionError("file.list_allowed is disabled (ALLOW_FILE_LIST=false)")

    resolved = validate_skill_path(path)

    if not resolved.exists():
        raise ValueError(f"Path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"Path is not a directory: {resolved}")

    max_items = min(int(max_items), 500)
    entries = []
    for item in sorted(resolved.iterdir()):
        try:
            stat = item.stat()
            entries.append({
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": stat.st_size if not item.is_dir() else None,
                "modified": stat.st_mtime,
                "suffix": item.suffix.lower() if not item.is_dir() else None,
            })
        except OSError:
            continue
        if len(entries) >= max_items:
            break

    return {
        "path": str(resolved),
        "count": len(entries),
        "truncated": len(entries) >= max_items,
        "entries": entries,
    }


def file_copy(
    src_path: str,
    dst_path: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy a single file to a new location.

    - src and dst must both be under ALLOWED_ROOTS
    - DENY_PATH_PATTERNS are blocked for both paths
    - Parent directories of dst are created automatically
    - overwrite=false raises an error if dst already exists
    - Directories are not supported; use src_path pointing to a file
    """
    from assetclaw_matting.config import settings
    if not settings.allow_file_copy:
        raise PermissionError("file.copy is disabled (ALLOW_FILE_COPY=false)")

    src = validate_skill_path(src_path)
    dst = validate_skill_path(dst_path)

    if not src.exists():
        raise ValueError(f"Source does not exist: {src}")
    if src.is_dir():
        raise ValueError(f"Source is a directory; only files are supported: {src}")

    dst_existed = dst.exists()
    if dst_existed and not overwrite:
        raise ValueError(
            f"Destination already exists: {dst}. Set overwrite=true to replace."
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))

    return {
        "src": str(src),
        "dst": str(dst),
        "size": dst.stat().st_size,
        "overwritten": dst_existed,
    }

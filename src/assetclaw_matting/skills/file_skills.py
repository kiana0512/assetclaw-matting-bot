"""Skill implementations for controlled file system access.

Security constraints:
- Only paths under ALLOWED_ROOTS are accessible.
- DENY_PATH_PATTERNS are always blocked.
- File content is never returned.
- File deletion is never permitted via skills.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

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

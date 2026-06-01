from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.security import has_denied_pattern, validate_path


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _matches_name_pattern(name: str, pattern: str) -> bool:
    name_l = name.lower()
    pattern_l = pattern.lower().strip()
    if not pattern_l:
        return False
    if pattern_l in name_l:
        return True

    # Feishu/LLM summaries may contain shortened names such as
    # img_v3_abc...608g.png. Treat "..." or "…" as ordered gaps.
    compact = pattern_l.replace("…", "...")
    if "..." not in compact:
        return False
    parts = [part for part in compact.split("...") if part]
    if not parts:
        return False
    pos = 0
    for part in parts:
        idx = name_l.find(part, pos)
        if idx < 0:
            return False
        pos = idx + len(part)
    return True


def find_paths_by_name(
    name_pattern: str,
    search_root: str | None = None,
    max_results: int = 30,
    max_depth: int = 5,
    files_only: bool = False,
) -> list[Path]:
    from assetclaw_matting.config import settings

    pattern = name_pattern.strip()
    if not pattern:
        raise ValueError("name_pattern is required")

    roots = [search_root] if search_root else settings.allowed_roots_list
    max_results = min(max(1, max_results), 100)
    max_depth = min(max(1, max_depth), 8)

    found: list[Path] = []
    for root_str in roots:
        try:
            root = validate_path(root_str, must_exist=True)
        except Exception:
            continue
        for dirpath, dirnames, filenames in os.walk(str(root)):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= max_depth:
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if not has_denied_pattern(Path(dirpath) / d)]
            names = filenames if files_only else filenames + dirnames
            for name in names:
                if not _matches_name_pattern(name, pattern):
                    continue
                full = Path(dirpath) / name
                if has_denied_pattern(full):
                    continue
                found.append(full)
                if len(found) >= max_results:
                    return found
    return found


def file_info(path: str) -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    stat = target.stat()
    return {
        "ok": True,
        "path": str(target),
        "exists": True,
        "is_dir": target.is_dir(),
        "is_file": target.is_file(),
        "size": None if target.is_dir() else stat.st_size,
        "modified_at": _iso_mtime(target),
        "suffix": target.suffix.lower() if target.is_file() else None,
        "parent": str(target.parent),
    }


def file_find_name(
    name_pattern: str,
    search_root: str | None = None,
    max_results: int = 30,
    max_depth: int = 5,
) -> dict[str, Any]:
    paths = find_paths_by_name(
        name_pattern=name_pattern,
        search_root=search_root,
        max_results=max_results,
        max_depth=max_depth,
    )
    items = [{"name": path.name, "path": str(path), "is_dir": path.is_dir()} for path in paths]
    return {
        "ok": True,
        "pattern": name_pattern,
        "count": len(items),
        "truncated": len(items) >= min(max(1, max_results), 100),
        "items": items,
    }


def file_tree(path: str, max_depth: int = 3, max_items: int = 100) -> dict[str, Any]:
    root = validate_path(path, must_exist=True)
    if not root.is_dir():
        raise ValueError("path must be a directory")

    max_depth = min(max(1, max_depth), 6)
    max_items = min(max(1, max_items), 200)
    total = [0]

    def _build(p: Path, depth: int) -> list[dict]:
        if depth > max_depth or total[0] >= max_items:
            return []
        items = []
        try:
            children = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return []
        for child in children:
            if has_denied_pattern(child):
                continue
            if total[0] >= max_items:
                break
            total[0] += 1
            node: dict[str, Any] = {"name": child.name, "is_dir": child.is_dir()}
            if child.is_dir():
                node["children"] = _build(child, depth + 1)
            items.append(node)
        return items

    tree = _build(root, 1)
    return {
        "ok": True,
        "root": str(root),
        "max_depth": max_depth,
        "total_nodes": total[0],
        "truncated": total[0] >= max_items,
        "tree": tree,
    }


def file_recent(
    hours: int = 24,
    search_root: str | None = None,
    max_results: int = 30,
    max_depth: int = 4,
) -> dict[str, Any]:
    import time
    from assetclaw_matting.config import settings

    hours = min(max(1, hours), 8760)  # cap at 1 year
    max_results = min(max(1, max_results), 100)
    max_depth = min(max(1, max_depth), 6)
    cutoff = time.time() - hours * 3600

    roots = [search_root] if search_root else settings.allowed_roots_list
    found: list[dict[str, Any]] = []

    for root_str in roots:
        try:
            root = validate_path(root_str, must_exist=True)
        except Exception:
            continue
        for dirpath, dirnames, filenames in os.walk(str(root)):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= max_depth:
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if not has_denied_pattern(Path(dirpath) / d)]
            for name in filenames:
                full = Path(dirpath) / name
                if has_denied_pattern(full):
                    continue
                try:
                    mtime = full.stat().st_mtime
                    if mtime >= cutoff:
                        found.append({
                            "name": name,
                            "path": str(full),
                            "modified_at": _iso_mtime(full),
                            "size": full.stat().st_size,
                        })
                        if len(found) >= max_results:
                            found.sort(key=lambda x: x["modified_at"], reverse=True)
                            return {"ok": True, "hours": hours, "count": len(found), "truncated": True, "items": found}
                except OSError:
                    continue

    found.sort(key=lambda x: x["modified_at"], reverse=True)
    return {"ok": True, "hours": hours, "count": len(found), "truncated": False, "items": found}

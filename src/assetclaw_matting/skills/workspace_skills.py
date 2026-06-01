from __future__ import annotations

import hashlib
import uuid
import shutil
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.security import has_denied_pattern, validate_path

TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".html", ".css", ".js", ".py", ".ps1", ".bat", ".ini", ".log"}


def workspace_roots(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    roots = []
    for root in settings.allowed_roots_list:
        path = validate_path(root, must_exist=False)
        roots.append({"path": str(path), "exists": path.exists(), "is_dir": path.is_dir()})
    return {"ok": True, "roots": roots}


def workspace_disk_usage(**_: Any) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    items = []
    for root in settings.allowed_roots_list:
        path = validate_path(root, must_exist=False)
        if not path.exists():
            items.append({"path": str(path), "exists": False})
            continue
        usage = shutil.disk_usage(path)
        items.append({
            "path": str(path),
            "exists": True,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        })
    return {"ok": True, "items": items}


def file_read_text(path: str, max_chars: int = 8000, encoding: str = "utf-8") -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError("path must be a file")
    if target.suffix.lower() not in TEXT_EXTS:
        raise ValueError("file extension is not allowed for text read")
    max_chars = min(max(1, max_chars), 50000)
    text = target.read_text(encoding=encoding, errors="replace")[:max_chars]
    return {"ok": True, "path": str(target), "chars": len(text), "truncated": target.stat().st_size > len(text), "text": text}


def file_write_text(
    path: str,
    content: str,
    overwrite: bool = False,
    encoding: str = "utf-8",
    create_parents: bool = True,
) -> dict[str, Any]:
    target = validate_path(path, must_exist=False)
    if target.suffix.lower() not in TEXT_EXTS:
        raise ValueError("file extension is not allowed for text write")
    if target.exists() and not overwrite:
        raise FileExistsError("destination already exists")
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return {"ok": True, "path": str(target), "size": target.stat().st_size}


def file_append_text(path: str, content: str, encoding: str = "utf-8", create: bool = True) -> dict[str, Any]:
    target = validate_path(path, must_exist=not create)
    if target.suffix.lower() not in TEXT_EXTS:
        raise ValueError("file extension is not allowed for text append")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding=encoding) as f:
        f.write(content)
    return {"ok": True, "path": str(target), "size": target.stat().st_size}


def file_hash(path: str, algorithm: str = "sha256") -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError("path must be a file")
    algo = algorithm.lower()
    if algo not in {"sha256", "md5"}:
        raise ValueError("unsupported hash algorithm")
    digest = hashlib.new(algo)
    with target.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"ok": True, "path": str(target), "algorithm": algo, "hash": digest.hexdigest(), "size": target.stat().st_size}


def file_batch_info(paths: list[str]) -> dict[str, Any]:
    if not paths:
        raise ValueError("paths is required")
    items = []
    for raw in paths[:200]:
        target = validate_path(raw, must_exist=False)
        exists = target.exists()
        items.append({
            "path": str(target),
            "exists": exists,
            "is_dir": target.is_dir() if exists else False,
            "is_file": target.is_file() if exists else False,
            "size": target.stat().st_size if exists and target.is_file() else None,
        })
    return {"ok": True, "count": len(items), "items": items}


def file_copy_tree(src_path: str, dst_path: str, overwrite: bool = False, max_files: int = 1000) -> dict[str, Any]:
    src = validate_path(src_path, must_exist=True)
    dst = validate_path(dst_path, must_exist=False)
    if not src.is_dir():
        raise ValueError("src_path must be a directory")
    if dst.exists() and not overwrite:
        raise FileExistsError("destination already exists")
    file_count = _count_files(src, max_files=max_files)
    if file_count > max_files:
        raise ValueError(f"too many files: {file_count} > {max_files}")
    shutil.copytree(src, dst, dirs_exist_ok=overwrite, ignore=_ignore_denied)
    return {"ok": True, "src_path": str(src), "dst_path": str(dst), "files": file_count}


def file_copy_many(items: list[dict[str, str]], overwrite: bool = False) -> dict[str, Any]:
    if not items:
        raise ValueError("items is required")
    completed = []
    for item in items[:200]:
        src = validate_path(item.get("src_path", ""), must_exist=True)
        dst = validate_path(item.get("dst_path", ""), must_exist=False)
        if src.is_dir():
            raise ValueError("file_copy_many only supports files")
        if dst.exists() and not overwrite:
            raise FileExistsError(f"destination already exists: {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        completed.append({"src_path": str(src), "dst_path": str(dst), "size": dst.stat().st_size})
    return {"ok": True, "count": len(completed), "items": completed}


def file_move_many(items: list[dict[str, str]], overwrite: bool = False) -> dict[str, Any]:
    if not items:
        raise ValueError("items is required")
    completed = []
    for item in items[:200]:
        src = validate_path(item.get("src_path", ""), must_exist=True)
        dst = validate_path(item.get("dst_path", ""), must_exist=False)
        if dst.exists() and not overwrite:
            raise FileExistsError(f"destination already exists: {dst}")
        if dst.exists() and overwrite:
            if dst.is_dir():
                raise ValueError("cannot overwrite an existing directory")
            dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        moved = Path(shutil.move(str(src), str(dst)))
        completed.append({"src_path": str(src), "dst_path": str(moved)})
    return {"ok": True, "count": len(completed), "items": completed}


def file_mkdir_many(paths: list[str], parents: bool = True, exist_ok: bool = True) -> dict[str, Any]:
    if not paths:
        raise ValueError("paths is required")
    completed = []
    for raw in paths[:200]:
        target = validate_path(raw, must_exist=False)
        target.mkdir(parents=parents, exist_ok=exist_ok)
        completed.append({"path": str(target), "exists": target.exists()})
    return {"ok": True, "count": len(completed), "items": completed}


def file_rename_many(items: list[dict[str, str]], overwrite: bool = False) -> dict[str, Any]:
    if not items:
        raise ValueError("items is required")
    sources = [validate_path(item.get("src_path", ""), must_exist=True) for item in items[:200]]
    targets = [validate_path(item.get("dst_path", ""), must_exist=False) for item in items[:200]]
    source_set = {str(source).lower() for source in sources}
    for source in sources:
        if not source.is_file():
            raise ValueError("all src_path values must be files")
    for target in targets:
        if target.exists() and str(target).lower() not in source_set and not overwrite:
            raise FileExistsError(f"destination already exists: {target}")

    temp_ops = []
    for source, target in zip(sources, targets):
        if source == target:
            temp_ops.append((source, source, target))
            continue
        temp = validate_path(source.parent / f".assetclaw_rename_{uuid.uuid4().hex}{source.suffix}", must_exist=False)
        source.rename(temp)
        temp_ops.append((source, temp, target))

    completed = []
    try:
        for source, temp, target in temp_ops:
            if temp != target:
                if target.exists() and overwrite and str(target).lower() not in source_set:
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                temp.rename(target)
            completed.append({"src_path": str(source), "dst_path": str(target)})
    except Exception:
        for source, temp, _target in reversed(temp_ops):
            if temp.exists() and not source.exists():
                temp.rename(source)
        raise
    return {"ok": True, "count": len(completed), "items": completed}


def file_delete(path: str, recursive: bool = False) -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if target.is_dir():
        if not recursive:
            target.rmdir()
        else:
            shutil.rmtree(target)
        return {"ok": True, "path": str(target), "deleted": True, "was_dir": True}
    target.unlink()
    return {"ok": True, "path": str(target), "deleted": True, "was_dir": False}


def file_empty_dir(path: str) -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if not target.is_dir():
        raise ValueError("path must be a directory")
    removed = 0
    for child in target.iterdir():
        if has_denied_pattern(child):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    return {"ok": True, "path": str(target), "removed": removed}


def file_unzip(zip_path: str, dst_dir: str, overwrite: bool = False, max_files: int = 1000) -> dict[str, Any]:
    import zipfile

    archive = validate_path(zip_path, must_exist=True)
    dst = validate_path(dst_dir, must_exist=False)
    if archive.suffix.lower() != ".zip":
        raise ValueError("only .zip archives are supported")
    dst.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(archive, "r") as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if len(infos) > max_files:
            raise ValueError(f"too many files: {len(infos)} > {max_files}")
        for info in infos:
            target = validate_path(dst / info.filename, must_exist=False)
            if has_denied_pattern(target):
                continue
            if target.exists() and not overwrite:
                raise FileExistsError(f"destination already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append(str(target))
    return {"ok": True, "zip_path": str(archive), "dst_dir": str(dst), "count": len(extracted), "items": extracted[:50]}


def file_rename_sequence(
    paths: list[str],
    start: int = 1,
    padding: int = 0,
    preserve_extension: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("paths is required")
    if len(paths) > 200:
        raise ValueError("too many files")

    sources = [validate_path(path, must_exist=True) for path in paths]
    if any(not source.is_file() for source in sources):
        raise ValueError("all paths must be files")
    if len({str(source).lower() for source in sources}) != len(sources):
        raise ValueError("duplicate source paths are not allowed")

    operations = []
    source_set = {str(source).lower() for source in sources}
    for index, source in enumerate(sources, start=start):
        stem = str(index).zfill(max(0, padding))
        suffix = source.suffix if preserve_extension else ""
        target = validate_path(source.parent / f"{stem}{suffix}", must_exist=False)
        if target.exists() and str(target).lower() not in source_set and not overwrite:
            raise FileExistsError(f"destination already exists: {target}")
        operations.append((source, target))

    temp_ops = []
    for source, target in operations:
        if source == target:
            temp_ops.append((source, source, target))
            continue
        temp = validate_path(source.parent / f".assetclaw_rename_{uuid.uuid4().hex}{source.suffix}", must_exist=False)
        source.rename(temp)
        temp_ops.append((source, temp, target))

    completed = []
    try:
        for source, temp, target in temp_ops:
            if temp == target:
                completed.append({"src_path": str(source), "dst_path": str(target)})
                continue
            if target.exists() and overwrite and str(target).lower() not in source_set:
                target.unlink()
            temp.rename(target)
            completed.append({"src_path": str(source), "dst_path": str(target)})
    except Exception:
        for source, temp, _target in reversed(temp_ops):
            if temp.exists() and not source.exists():
                temp.rename(source)
        raise

    return {"ok": True, "count": len(completed), "items": completed}


def _count_files(root: Path, max_files: int) -> int:
    count = 0
    for item in root.rglob("*"):
        if has_denied_pattern(item):
            continue
        if item.is_file():
            count += 1
            if count > max_files:
                return count
    return count


def _ignore_denied(directory: str, names: list[str]) -> set[str]:
    base = Path(directory)
    return {name for name in names if has_denied_pattern(base / name)}

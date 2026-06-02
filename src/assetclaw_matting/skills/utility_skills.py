from __future__ import annotations

import csv
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.media_skills import ARCHIVE_EXTS, IMAGE_EXTS, TABLE_EXTS, VIDEO_EXTS
from assetclaw_matting.skills.security import has_denied_pattern, validate_path
from assetclaw_matting.skills.workspace_skills import TEXT_EXTS


def file_search_text(
    path: str,
    query: str,
    extensions: list[str] | None = None,
    max_results: int = 50,
    max_depth: int = 5,
    context_chars: int = 80,
) -> dict[str, Any]:
    root = validate_path(path, must_exist=True)
    if not query.strip():
        raise ValueError("query is required")
    if not root.is_dir():
        raise ValueError("path must be a directory")
    exts = _normalize_exts(extensions) if extensions else TEXT_EXTS
    max_results = max(1, min(max_results, 200))
    max_depth = max(1, min(max_depth, 8))
    context_chars = max(20, min(context_chars, 300))
    needle = query.lower()
    items: list[dict[str, Any]] = []
    for file_path in _walk_files(root, max_depth=max_depth):
        if file_path.suffix.lower() not in exts:
            continue
        if file_path.stat().st_size > 5 * 1024 * 1024:
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        start = 0
        while len(items) < max_results:
            idx = low.find(needle, start)
            if idx < 0:
                break
            line_no = text.count("\n", 0, idx) + 1
            begin = max(0, idx - context_chars)
            end = min(len(text), idx + len(query) + context_chars)
            snippet = text[begin:end].replace("\r", "").replace("\n", " ")
            items.append({
                "path": str(file_path),
                "name": file_path.name,
                "line": line_no,
                "snippet": snippet,
            })
            start = idx + len(query)
        if len(items) >= max_results:
            break
    return {"ok": True, "path": str(root), "query": query, "count": len(items), "truncated": len(items) >= max_results, "items": items}


def file_preview(path: str, max_chars: int = 3000, tail: bool = False) -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError("path must be a file")
    max_chars = max(100, min(max_chars, 20000))
    suffix = target.suffix.lower()
    if suffix in TEXT_EXTS:
        text = target.read_text(encoding="utf-8", errors="replace")
        preview = text[-max_chars:] if tail else text[:max_chars]
        return {
            "ok": True,
            "path": str(target),
            "kind": "text",
            "chars": len(preview),
            "truncated": len(text) > len(preview),
            "preview": preview,
        }
    with target.open("rb") as fh:
        data = fh.read(min(max_chars, 512))
    return {
        "ok": True,
        "path": str(target),
        "kind": "binary",
        "size": target.stat().st_size,
        "hex": data.hex(" ")[: max_chars],
    }


def file_count(path: str, recursive: bool = True, max_depth: int = 8) -> dict[str, Any]:
    root = validate_path(path, must_exist=True)
    if not root.is_dir():
        raise ValueError("path must be a directory")
    max_depth = max(1, min(max_depth, 12))
    stats = {
        "dirs": 0,
        "files": 0,
        "images": 0,
        "videos": 0,
        "tables": 0,
        "archives": 0,
        "text": 0,
        "bytes": 0,
    }
    iterator = _walk_files(root, max_depth=max_depth) if recursive else [p for p in root.iterdir() if p.is_file()]
    for item in iterator:
        if has_denied_pattern(item):
            continue
        suffix = item.suffix.lower()
        stats["files"] += 1
        stats["bytes"] += item.stat().st_size
        if suffix in IMAGE_EXTS:
            stats["images"] += 1
        elif suffix in VIDEO_EXTS:
            stats["videos"] += 1
        elif suffix in TABLE_EXTS:
            stats["tables"] += 1
        elif suffix in ARCHIVE_EXTS:
            stats["archives"] += 1
        elif suffix in TEXT_EXTS:
            stats["text"] += 1
    if recursive:
        for dirpath, dirnames, _filenames in os.walk(str(root)):
            current = Path(dirpath)
            if len(current.relative_to(root).parts) >= max_depth:
                dirnames.clear()
                continue
            dirnames[:] = [name for name in dirnames if not has_denied_pattern(current / name)]
            stats["dirs"] += len(dirnames)
    else:
        stats["dirs"] = sum(1 for p in root.iterdir() if p.is_dir() and not has_denied_pattern(p))
    return {"ok": True, "path": str(root), "recursive": recursive, **stats}


def file_manifest(
    path: str,
    output_path: str,
    recursive: bool = True,
    max_items: int = 5000,
    format: str = "json",
) -> dict[str, Any]:
    root = validate_path(path, must_exist=True)
    output = validate_path(output_path, must_exist=False)
    if not root.is_dir():
        raise ValueError("path must be a directory")
    fmt = format.lower().strip() or output.suffix.lower().lstrip(".") or "json"
    if fmt not in {"json", "csv"}:
        raise ValueError("format must be json or csv")
    if output.suffix.lower() not in {".json", ".csv"}:
        raise ValueError("output_path must end with .json or .csv")
    max_items = max(1, min(max_items, 20000))
    items = []
    files = _walk_files(root, max_depth=12) if recursive else [p for p in root.iterdir() if p.is_file()]
    for file_path in files[:max_items]:
        stat = file_path.stat()
        items.append({
            "relative_path": str(file_path.relative_to(root)),
            "path": str(file_path),
            "name": file_path.name,
            "extension": file_path.suffix.lower(),
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        output.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with output.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["relative_path", "path", "name", "extension", "size", "modified_at"])
            writer.writeheader()
            writer.writerows(items)
    return {"ok": True, "path": str(root), "output_path": str(output), "count": len(items), "truncated": len(files) > len(items), "format": fmt}


def archive_list(path: str, max_items: int = 200) -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if target.suffix.lower() != ".zip":
        raise ValueError("only .zip archives are supported")
    max_items = max(1, min(max_items, 1000))
    items = []
    with zipfile.ZipFile(target, "r") as zf:
        infos = zf.infolist()
        for info in infos[:max_items]:
            items.append({
                "name": info.filename,
                "is_dir": info.is_dir(),
                "size": info.file_size,
                "compressed_size": info.compress_size,
            })
    return {"ok": True, "path": str(target), "count": len(items), "total": len(infos), "truncated": len(infos) > len(items), "items": items}


def json_query(path: str, pointer: str = "") -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if target.suffix.lower() != ".json":
        raise ValueError("path must be a .json file")
    data = json.loads(target.read_text(encoding="utf-8"))
    value = _json_pointer(data, pointer)
    return {"ok": True, "path": str(target), "pointer": pointer, "value": value}


def csv_summary(path: str, max_rows: int = 5) -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if target.suffix.lower() not in {".csv", ".tsv"}:
        raise ValueError("path must be .csv or .tsv")
    delimiter = "\t" if target.suffix.lower() == ".tsv" else ","
    max_rows = max(1, min(max_rows, 20))
    with target.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        rows = []
        for index, row in enumerate(reader):
            if index < max_rows:
                rows.append(dict(row))
        return {
            "ok": True,
            "path": str(target),
            "columns": reader.fieldnames or [],
            "sample_rows": rows,
            "sample_count": len(rows),
        }


def _walk_files(root: Path, max_depth: int) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        current = Path(dirpath)
        if len(current.relative_to(root).parts) >= max_depth:
            dirnames.clear()
            continue
        dirnames[:] = [name for name in dirnames if not has_denied_pattern(current / name)]
        for name in filenames:
            path = current / name
            if not has_denied_pattern(path):
                files.append(path)
    return sorted(files, key=lambda p: str(p).lower())


def _normalize_exts(extensions: list[str]) -> set[str]:
    return {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}


def _json_pointer(data: Any, pointer: str) -> Any:
    if not pointer:
        return data
    current = data
    for raw_part in pointer.strip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current

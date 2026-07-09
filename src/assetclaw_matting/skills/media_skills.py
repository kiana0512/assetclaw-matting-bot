from __future__ import annotations

import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from assetclaw_matting.runtime_context import get_runtime_context
from assetclaw_matting.skills.security import has_denied_pattern, validate_path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
TABLE_EXTS = {".xlsx", ".xls", ".csv", ".tsv"}
ARCHIVE_EXTS = {".zip", ".7z", ".rar"}


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _walk_files(root: Path, recursive: bool, max_depth: int) -> list[Path]:
    if not recursive:
        return [p for p in root.iterdir() if p.is_file() and not has_denied_pattern(p)]
    files: list[Path] = []
    max_depth = min(max(1, max_depth), 8)
    for dirpath, dirnames, filenames in os.walk(str(root)):
        current = Path(dirpath)
        depth = len(current.relative_to(root).parts)
        if depth >= max_depth:
            dirnames.clear()
            continue
        dirnames[:] = [d for d in dirnames if not has_denied_pattern(current / d)]
        for name in filenames:
            full = current / name
            if not has_denied_pattern(full):
                files.append(full)
    return files


def _type_exts(kind: str, extensions: list[str] | None) -> set[str]:
    if extensions:
        return {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    normalized = kind.strip().lower()
    if normalized in {"image", "images", "图片"}:
        return IMAGE_EXTS
    if normalized in {"video", "videos", "视频"}:
        return VIDEO_EXTS
    if normalized in {"table", "tables", "表格"}:
        return TABLE_EXTS
    if normalized in {"archive", "archives", "压缩包"}:
        return ARCHIVE_EXTS
    return {normalized if normalized.startswith(".") else f".{normalized}"}


def file_list_by_type(
    path: str,
    kind: str = "image",
    extensions: list[str] | None = None,
    recursive: bool = False,
    max_results: int = 50,
    max_depth: int = 4,
) -> dict[str, Any]:
    root = validate_path(path, must_exist=True)
    if not root.is_dir():
        raise ValueError("path must be a directory")
    exts = _type_exts(kind, extensions)
    max_results = min(max(1, max_results), 200)
    items: list[dict[str, Any]] = []
    for full in sorted(_walk_files(root, recursive, max_depth), key=lambda p: p.name.lower()):
        if full.suffix.lower() not in exts:
            continue
        stat = full.stat()
        items.append({
            "name": full.name,
            "path": str(full),
            "extension": full.suffix.lower(),
            "size": stat.st_size,
            "modified_at": _iso_mtime(full),
        })
        if len(items) >= max_results:
            break
    return {
        "ok": True,
        "path": str(root),
        "kind": kind,
        "extensions": sorted(exts),
        "recursive": recursive,
        "count": len(items),
        "truncated": len(items) >= max_results,
        "items": items,
    }


def image_list(path: str, recursive: bool = False, max_results: int = 50, max_depth: int = 4) -> dict[str, Any]:
    return file_list_by_type(
        path=path,
        kind="image",
        recursive=recursive,
        max_results=max_results,
        max_depth=max_depth,
    )


def image_info(path: str) -> dict[str, Any]:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError("path must be an image file")
    if target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("unsupported image extension")
    with Image.open(target) as img:
        return {
            "ok": True,
            "path": str(target),
            "name": target.name,
            "format": img.format,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "size": target.stat().st_size,
            "modified_at": _iso_mtime(target),
        }


def image_batch_info(paths: list[str]) -> dict[str, Any]:
    if not paths:
        raise ValueError("paths is required")
    items = []
    for raw in paths[:200]:
        target = validate_path(raw, must_exist=True)
        if target.suffix.lower() not in IMAGE_EXTS or not target.is_file():
            raise ValueError(f"unsupported image file: {target}")
        with Image.open(target) as img:
            items.append({
                "path": str(target),
                "name": target.name,
                "format": img.format,
                "mode": img.mode,
                "width": img.width,
                "height": img.height,
                "size": target.stat().st_size,
            })
    return {"ok": True, "count": len(items), "items": items}


def image_convert_format(
    src_path: str,
    dst_path: str,
    format: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    src = validate_path(src_path, must_exist=True)
    dst = validate_path(dst_path, must_exist=False)
    if src.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("unsupported source image extension")
    if dst.exists() and not overwrite:
        raise FileExistsError("destination already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    fmt = (format or dst.suffix.lstrip(".")).upper()
    if fmt == "JPG":
        fmt = "JPEG"
    with Image.open(src) as img:
        if fmt == "JPEG" and img.mode in {"RGBA", "LA", "P"}:
            img = img.convert("RGB")
        img.save(dst, format=fmt)
    return {"ok": True, "src_path": str(src), "dst_path": str(dst), "format": fmt, "size": dst.stat().st_size}


def image_resize(
    src_path: str,
    dst_path: str,
    width: int,
    height: int,
    overwrite: bool = False,
) -> dict[str, Any]:
    src = validate_path(src_path, must_exist=True)
    dst = validate_path(dst_path, must_exist=False)
    if src.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("unsupported source image extension")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if dst.exists() and not overwrite:
        raise FileExistsError("destination already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        resized = img.resize((min(width, 10000), min(height, 10000)))
        if dst.suffix.lower() in {".jpg", ".jpeg"} and resized.mode in {"RGBA", "LA", "P"}:
            resized = resized.convert("RGB")
        resized.save(dst)
    return {"ok": True, "src_path": str(src), "dst_path": str(dst), "width": width, "height": height, "size": dst.stat().st_size}


def file_copy_as(
    src_path: str,
    new_name: str,
    dst_dir: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    import shutil

    src = validate_path(src_path, must_exist=True)
    if not src.is_file():
        raise ValueError("src_path must be a file")
    clean_name = Path(new_name.strip()).name
    if not clean_name:
        raise ValueError("new_name is required")
    target_dir = validate_path(dst_dir, must_exist=True) if dst_dir else src.parent
    if not target_dir.is_dir():
        raise ValueError("dst_dir must be a directory")
    dst = validate_path(target_dir / clean_name, must_exist=False)
    if dst.exists() and not overwrite:
        raise FileExistsError("destination already exists")
    shutil.copy2(src, dst)
    return {"ok": True, "src_path": str(src), "dst_path": str(dst), "size": dst.stat().st_size}


def file_duplicate_same_dir(src_path: str, suffix: str = "_copy", overwrite: bool = False) -> dict[str, Any]:
    src = validate_path(src_path, must_exist=True)
    if not src.is_file():
        raise ValueError("src_path must be a file")
    suffix = suffix.strip() or "_copy"
    new_name = f"{src.stem}{suffix}{src.suffix}"
    return file_copy_as(str(src), new_name, str(src.parent), overwrite=overwrite)


def file_zip_paths(paths: list[str], zip_path: str, overwrite: bool = False) -> dict[str, Any]:
    if not paths:
        raise ValueError("paths is required")
    zip_target = validate_path(zip_path, must_exist=False)
    if zip_target.suffix.lower() != ".zip":
        raise ValueError("zip_path must end with .zip")
    if zip_target.exists() and not overwrite:
        raise FileExistsError("zip file already exists")
    zip_target.parent.mkdir(parents=True, exist_ok=True)
    added: list[str] = []
    with zipfile.ZipFile(zip_target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for raw in paths[:100]:
            item = validate_path(raw, must_exist=True)
            if item.is_file():
                zf.write(item, arcname=item.name)
                added.append(str(item))
            elif item.is_dir():
                for full in _walk_files(item, recursive=True, max_depth=6):
                    zf.write(full, arcname=str(full.relative_to(item.parent)))
                    added.append(str(full))
                    if len(added) >= 500:
                        break
    return {"ok": True, "zip_path": str(zip_target), "count": len(added), "items": added[:50]}


def feishu_zip_and_send(paths: list[str], zip_path: str, overwrite: bool = True, file_name: str | None = None) -> dict[str, Any]:
    result = file_zip_paths(paths=paths, zip_path=zip_path, overwrite=overwrite)
    sent = feishu_send_file(result["zip_path"], file_name=file_name)
    return {
        "ok": True,
        "zip_path": result["zip_path"],
        "count": result["count"],
        "file_name": sent["file_name"],
        "size": sent["size"],
        "chat_id": sent["chat_id"],
    }


def feishu_send_file(path: str, file_name: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.feishu.client import feishu_client

    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError("path must be a file")
    ctx = get_runtime_context()
    chat_id = ctx.get("chat_id")
    if not chat_id:
        raise RuntimeError("feishu_send_file requires a Feishu chat context")
    sent_name = file_name.strip() if file_name else target.name
    feishu_client.send_file_to_chat(chat_id, target, sent_name)
    return {"ok": True, "chat_id": chat_id, "path": str(target), "file_name": sent_name, "size": target.stat().st_size}


def feishu_send_image(path: str) -> dict[str, Any]:
    from assetclaw_matting.feishu.client import feishu_client

    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError("path must be an image file")
    if target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError("unsupported image extension")
    ctx = get_runtime_context()
    chat_id = ctx.get("chat_id")
    if not chat_id:
        raise RuntimeError("feishu_send_image requires a Feishu chat context")
    feishu_client.send_image_to_chat(chat_id, target)
    return {"ok": True, "chat_id": chat_id, "path": str(target), "file_name": target.name, "size": target.stat().st_size}


def feishu_send_file_by_name(
    name_pattern: str,
    search_root: str | None = None,
    file_name: str | None = None,
    max_depth: int = 5,
) -> dict[str, Any]:
    from assetclaw_matting.skills.file_extra_skills import find_paths_by_name

    matches = find_paths_by_name(
        name_pattern=name_pattern,
        search_root=search_root,
        max_results=5,
        max_depth=max_depth,
        files_only=True,
    )
    if not matches:
        raise FileNotFoundError(f"没有找到匹配文件：{name_pattern}")
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches[:5])
        raise ValueError(f"匹配到多个文件，请说完整一点：{names}")
    return feishu_send_file(str(matches[0]), file_name=file_name)


def feishu_send_image_by_name(
    name_pattern: str,
    search_root: str | None = None,
    max_depth: int = 5,
) -> dict[str, Any]:
    from assetclaw_matting.skills.file_extra_skills import find_paths_by_name

    matches = [
        path for path in find_paths_by_name(
            name_pattern=name_pattern,
            search_root=search_root,
            max_results=5,
            max_depth=max_depth,
            files_only=True,
        )
        if path.suffix.lower() in IMAGE_EXTS
    ]
    if not matches:
        raise FileNotFoundError(f"没有找到匹配图片：{name_pattern}")
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches[:5])
        raise ValueError(f"匹配到多张图片，请说完整一点：{names}")
    return feishu_send_image(str(matches[0]))

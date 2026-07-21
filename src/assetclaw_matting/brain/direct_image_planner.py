from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall
from assetclaw_matting.config import settings
from assetclaw_matting.skills.media_skills import IMAGE_EXTS

ARCHIVE_EXTS = {".zip"}
MAX_IMAGE_SET_ITEMS = 1000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


STATUS_WORDS = (
    "图片处理进度",
    "图处理进度",
    "这张图处理",
    "这个图片处理",
    "直传图片进度",
    "进度如何",
    "进度怎么样",
    "进度咋样",
    "到哪了",
    "处理到哪",
)


def plan_direct_image_task(message: BrainMessage) -> tuple[list[ToolCall], str] | tuple[None, str] | None:
    text = (message.text or "").strip()
    if _asks_status(text):
        return [ToolCall(skill="direct_image.status", arguments={})], "direct image status route"

    images = _image_attachments(message.attachments)
    if not images and _asks_image_matting(text):
        images = _recent_image_set(message.conversation_id)
    if not images:
        return None
    missing = [item for item in images if not item.get("local_path")]
    if missing:
        return None, "我收到了图片/图片合集，但还没有拿到本地文件，等下载完成后再处理。"

    paths = [str(item["local_path"]) for item in images if item.get("local_path")]
    names = [str(item.get("file_name") or Path(path).name) for item, path in zip(images, paths)]
    collections = [str(item.get("source_collection") or "").strip() for item in images if item.get("source_collection")]
    package_as_sequence = len(paths) > 1 or any(bool(item.get("sequence_source")) for item in images)
    if len(paths) > 1 and collections and len(set(collections)) == 1:
        run_label = collections[0]
    elif len(paths) > 1:
        run_label = f"{len(paths)}张序列帧"
    else:
        run_label = names[0]
    return (
        [
            ToolCall(
                skill="direct_image.start",
                arguments={
                    "image_paths": paths,
                    "source_names": names,
                    "run_label": run_label,
                    "package_as_sequence": package_as_sequence,
                },
            )
        ],
        "direct Feishu image attachment route",
    )


def _asks_status(text: str) -> bool:
    if not text:
        return False
    return any(word in text for word in STATUS_WORDS) or (
        "处理进度" in text and any(word in text for word in ("图片", "图"))
    )


def _image_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images = []
    for item in items or []:
        raw_type = str(item.get("type") or "").lower()
        path = str(item.get("local_path") or item.get("file_name") or "")
        suffix = Path(path).suffix.lower()
        if raw_type == "image" or suffix in IMAGE_EXTS:
            images.append(item)
            continue
        images.extend(_expand_image_set_attachment(item))
    return images


def _expand_image_set_attachment(item: dict[str, Any]) -> list[dict[str, Any]]:
    path_text = str(item.get("local_path") or "").strip()
    if not path_text:
        return []
    path = Path(path_text)
    image_paths = _image_paths_from_source(path)
    if not image_paths:
        return []
    source_name = str(item.get("file_name") or path.name or "图片合集")
    return [
        {
            **item,
            "type": "image",
            "local_path": str(image_path),
            "file_name": image_path.name,
            "source_collection": source_name,
            "sequence_source": True,
        }
        for image_path in image_paths[:MAX_IMAGE_SET_ITEMS]
    ]


def _image_paths_from_source(path: Path) -> list[Path]:
    if path.is_dir():
        return _image_paths_in_dir(path)
    if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
        return [path]
    if path.is_file() and path.suffix.lower() in ARCHIVE_EXTS:
        return _extract_archive_images(path)
    return []


def _image_paths_in_dir(path: Path) -> list[Path]:
    try:
        images = [item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in IMAGE_EXTS]
    except OSError:
        return []
    return sorted(images, key=lambda item: _natural_path_key(item, path))[:MAX_IMAGE_SET_ITEMS]


def _extract_archive_images(path: Path) -> list[Path]:
    try:
        stat = path.stat()
        fingerprint = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        return []
    digest = hashlib.sha1(fingerprint.encode("utf-8", errors="ignore")).hexdigest()[:10]
    target_root = Path(settings.storage_dir) / "direct_image_imports" / f"{path.stem}_{digest}"
    if _image_paths_in_dir(target_root):
        return _image_paths_in_dir(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    try:
        with zipfile.ZipFile(path) as archive:
            total_size = 0
            for member in archive.infolist():
                if member.is_dir():
                    continue
                member_path = Path(member.filename.replace("\\", "/"))
                if member_path.suffix.lower() not in IMAGE_EXTS:
                    continue
                if any(part in {"", ".", ".."} for part in member_path.parts):
                    continue
                total_size += max(0, int(member.file_size or 0))
                if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    return []
                safe_parts = [part for part in member_path.parts if part not in {"", ".", ".."}]
                if not safe_parts:
                    continue
                target = target_root.joinpath(*safe_parts)
                if not _is_relative_to(target.resolve(), target_root.resolve()):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                extracted.append(target)
                if len(extracted) >= MAX_IMAGE_SET_ITEMS:
                    break
    except (OSError, zipfile.BadZipFile):
        return []
    return sorted(extracted, key=lambda item: _natural_path_key(item, target_root))


def _natural_path_key(path: Path, root: Path) -> tuple[tuple[tuple[int, object], ...], ...]:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return tuple(_natural_text_key(part) for part in relative.parts)


def _natural_text_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(token)) if token.isdigit() else (1, token.casefold())
        for token in re.split(r"(\d+)", value)
        if token
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _asks_image_matting(text: str) -> bool:
    if not text:
        return False
    return any(word in text for word in ("抠图", "扣图", "去背景", "透明底", "处理图片", "图片处理", "序列帧"))


def _recent_image_set(conversation_id: str) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    from assetclaw_matting.db.repos import list_memory_notes

    for note in list_memory_notes(conversation_id, limit=30):
        if note.get("key") not in {"last_image_set_path", "last_image_path"}:
            continue
        path = Path(str(note.get("value") or ""))
        image_paths = _image_paths_from_source(path)
        if not image_paths:
            continue
        source_name = path.name or "图片合集"
        return [{"type": "image", "local_path": str(item), "file_name": item.name, "source_collection": source_name} for item in image_paths]
    return []

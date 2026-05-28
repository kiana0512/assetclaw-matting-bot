from __future__ import annotations

import logging
from pathlib import Path

from assetclaw_matting.config import settings
from assetclaw_matting.models.task_models import Task

log = logging.getLogger(__name__)

_ALLOWED_INPUT_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


# ── Path validation ───────────────────────────────────────────────────────────

def validate_allowed_path(path: Path) -> None:
    """Raise ValueError if path is not under any configured allowed root."""
    roots = settings.allowed_roots_list
    if not roots:
        return  # No restriction configured
    resolved = str(path.resolve())
    for root in roots:
        if resolved.startswith(str(Path(root).resolve())):
            return
    raise ValueError(
        f"Path '{path}' is not under any allowed root. "
        f"Allowed: {roots}"
    )


def _safe_path(base: Path, *parts: str) -> Path:
    """Resolve a path and verify it stays within base."""
    candidate = (base / Path(*parts)).resolve()
    base_resolved = base.resolve()
    if not str(candidate).startswith(str(base_resolved)):
        raise ValueError(f"Path escape attempt: {candidate}")
    return candidate


# ── Task metadata directory ───────────────────────────────────────────────────

def create_task_meta_dir(task_id: str) -> Path:
    """Create and return the metadata directory for a task."""
    task_dir = _safe_path(settings.tasks_dir, task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def write_task_json(task: Task) -> None:
    task_dir = create_task_meta_dir(task.id)
    (task_dir / "task.json").write_text(
        task.model_dump_json(indent=2), encoding="utf-8"
    )


def save_debug_file(task_id: str, filename: str, content: str) -> Path:
    """Save a debug file in the task's metadata directory."""
    task_dir = create_task_meta_dir(task_id)
    dest = task_dir / filename
    dest.write_text(content, encoding="utf-8")
    return dest


def save_debug_history(task_id: str, history_json: str) -> Path:
    dest = settings.debug_dir / f"history_{task_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(history_json, encoding="utf-8")
    log.warning("Saved debug history to %s", dest)
    return dest


# ── Batch image scanning ──────────────────────────────────────────────────────

def scan_images(input_dir: Path) -> list[Path]:
    """Return sorted list of supported image files in input_dir."""
    return sorted(
        f for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _ALLOWED_INPUT_EXTS
    )


def compute_output_path(output_dir: Path, input_path: Path) -> Path:
    """Compute the expected output path for an input image."""
    stem = input_path.stem
    return output_dir / f"{stem}_matting.png"

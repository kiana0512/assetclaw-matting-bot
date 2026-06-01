from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Any

from assetclaw_matting.skills.security import validate_path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _batch_id() -> str:
    return "BATCH_" + uuid.uuid4().hex[:12].upper()


def _count_images(path: Path) -> int:
    return sum(1 for item in path.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES)


def batch_create(
    input_dir: str,
    output_dir: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    src = validate_path(input_dir, must_exist=True)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    dst = validate_path(output_dir or str(settings.default_batch_output_dir), must_exist=False)
    dst.mkdir(parents=True, exist_ok=True)

    batch_id = _batch_id()
    total = _count_images(src)
    created_at = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO batches (id, status, input_dir, output_dir, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (batch_id, "CREATED", str(src), str(dst), created_at),
        )
    return {
        "ok": True,
        "implemented": True,
        "partial": True,
        "fake_mode": settings.comfyui_fake_mode,
        "batch_id": batch_id,
        "status": "CREATED",
        "input_dir": str(src),
        "output_dir": str(dst),
        "image_count": total,
        "note": note,
    }


def batch_start(batch_id: str) -> dict[str, Any]:
    return _set_status(batch_id, "RUNNING")


def batch_status(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, status, input_dir, output_dir, created_at FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
    if not row:
        return {"ok": False, "error": f"batch not found: {batch_id}"}
    image_count = _count_images(Path(row["input_dir"])) if Path(row["input_dir"]).exists() else 0
    return {
        "ok": True,
        "implemented": True,
        "partial": True,
        "fake_mode": settings.comfyui_fake_mode,
        "batch_id": row["id"],
        "status": row["status"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "image_count": image_count,
        "created_at": row["created_at"],
    }


def batch_pause(batch_id: str) -> dict[str, Any]:
    return _set_status(batch_id, "PAUSED")


def batch_resume(batch_id: str) -> dict[str, Any]:
    return _set_status(batch_id, "RUNNING")


def batch_cancel(batch_id: str) -> dict[str, Any]:
    return _set_status(batch_id, "CANCELED")


def batch_list(limit: int = 20) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    limit = min(max(1, limit), 100)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, status, input_dir, output_dir, created_at FROM batches ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "ok": True,
        "partial": True,
        "fake_mode": settings.comfyui_fake_mode,
        "count": len(rows),
        "batches": [dict(r) for r in rows],
    }


def batch_detail(batch_id: str) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, status, input_dir, output_dir, created_at FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
    if not row:
        return {"ok": False, "error": f"batch not found: {batch_id}"}
    image_count = _count_images(Path(row["input_dir"])) if Path(row["input_dir"]).exists() else 0
    return {
        "ok": True,
        "partial": True,
        "fake_mode": settings.comfyui_fake_mode,
        "batch_id": row["id"],
        "status": row["status"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "image_count": image_count,
        "created_at": row["created_at"],
    }


def _set_status(batch_id: str, status: str) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not existing:
            return {"ok": False, "error": f"batch not found: {batch_id}"}
        conn.execute("UPDATE batches SET status = ? WHERE id = ?", (status, batch_id))
    return {
        "ok": True,
        "implemented": True,
        "partial": True,
        "fake_mode": settings.comfyui_fake_mode,
        "batch_id": batch_id,
        "status": status,
    }

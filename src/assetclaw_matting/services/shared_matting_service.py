from __future__ import annotations

import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.services.notification_service import send_text
from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.security import validate_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "SMAT_" + uuid.uuid4().hex[:12].upper()


def start_shared_matting_run(
    workflow_path: str | None,
    shared_input_dir: str,
    shared_output_dir: str,
    chat_id: str = "",
    notify_interval_seconds: int = 60,
    max_images: int = 500,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.skills.comfyui_skills import run_start

    workflow = validate_path(workflow_path or str(settings.comfyui_workflow_path), must_exist=True)
    shared_input = validate_path(shared_input_dir, must_exist=True)
    shared_output = validate_path(shared_output_dir, must_exist=False)
    if not shared_input.is_dir():
        raise ValueError("shared_input_dir must be a directory")
    shared_output.mkdir(parents=True, exist_ok=True)

    images = [p for p in sorted(shared_input.iterdir(), key=lambda p: p.name.lower()) if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    images = images[: max(1, min(max_images, 2000))]
    if not images:
        raise ValueError("shared_input_dir has no supported images")

    run_id = _run_id()
    local_root = settings.storage_dir / "matting_runs" / run_id
    local_input = local_root / "input"
    local_output = local_root / "output"
    local_input.mkdir(parents=True, exist_ok=True)
    local_output.mkdir(parents=True, exist_ok=True)
    copied = _copy_images(images, local_input)

    comfy = run_start(
        workflow_path=str(workflow),
        input_dir=str(local_input),
        output_dir=str(local_output),
        max_images=len(copied),
        external_batch_id=f"assetclaw:{run_id}:matting:g1",
    )
    created_at = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO shared_matting_runs
            (id, status, workflow_path, shared_input_dir, shared_output_dir, local_input_dir, local_output_dir,
             comfyui_run_id, total, copied_in, synced_out, chat_id, notify_interval_seconds, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "RUNNING",
                str(workflow),
                str(shared_input),
                str(shared_output),
                str(local_input),
                str(local_output),
                comfy["run_id"],
                len(copied),
                len(copied),
                0,
                chat_id,
                max(10, min(notify_interval_seconds, 3600)),
                "",
                created_at,
                created_at,
            ),
        )

    if chat_id:
        send_text(chat_id, f"抠图任务已开始：{run_id}\n输入：{shared_input}\n输出：{shared_output}\n总数：{len(copied)} 张")
    _start_monitor_thread(run_id)
    return {
        "ok": True,
        "run_id": run_id,
        "status": "RUNNING",
        "comfyui_run_id": comfy["run_id"],
        "workflow_path": str(workflow),
        "shared_input_dir": str(shared_input),
        "shared_output_dir": str(shared_output),
        "local_input_dir": str(local_input),
        "local_output_dir": str(local_output),
        "total": len(copied),
        "notify_interval_seconds": max(10, min(notify_interval_seconds, 3600)),
    }


def shared_matting_status(run_id: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.skills.comfyui_skills import run_status

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "shared matting run not found"}
    comfy_status = run_status(row["comfyui_run_id"], include_gpu=True) if row["comfyui_run_id"] else {}
    return {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "workflow_path": row["workflow_path"],
        "shared_input_dir": row["shared_input_dir"],
        "shared_output_dir": row["shared_output_dir"],
        "local_input_dir": row["local_input_dir"],
        "local_output_dir": row["local_output_dir"],
        "comfyui_run_id": row["comfyui_run_id"],
        "total": row["total"],
        "copied_in": row["copied_in"],
        "synced_out": row["synced_out"],
        "error": row["error"],
        "comfyui": comfy_status,
    }


def sync_shared_outputs(run_id: str) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.skills.comfyui_skills import run_sync_outputs

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "shared matting run not found"}
    local_output = validate_path(row["local_output_dir"], must_exist=False)
    shared_output = validate_path(row["shared_output_dir"], must_exist=False)
    if row["comfyui_run_id"]:
        run_sync_outputs(row["comfyui_run_id"], overwrite=True)
    shared_output.mkdir(parents=True, exist_ok=True)
    copied = _copy_images([p for p in local_output.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], shared_output)
    with get_connection() as conn:
        conn.execute(
            "UPDATE shared_matting_runs SET synced_out = ?, updated_at = ? WHERE id = ?",
            (len(copied), _now(), run_id),
        )
    return {"ok": True, "run_id": run_id, "shared_output_dir": str(shared_output), "count": len(copied)}


def _copy_images(paths: list[Path], dst_dir: Path) -> list[Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for src in paths:
        target = dst_dir / src.name
        shutil.copy2(src, target)
        copied.append(target)
    return copied


def _start_monitor_thread(run_id: str) -> None:
    thread = threading.Thread(target=_monitor_loop, args=(run_id,), daemon=True, name=f"shared-matting-{run_id}")
    thread.start()


def _monitor_loop(run_id: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.config import settings

    last_text = ""
    while True:
        row = _get_run(run_id)
        if not row or row["status"] in {"DONE", "FAILED", "CANCELED"}:
            return
        try:
            status = shared_matting_status(run_id)
            text = _format_progress(status)
            if text != last_text and row["chat_id"]:
                send_text(row["chat_id"], text)
                last_text = text
            if settings.comfyui_fake_mode:
                _fake_complete_outputs(row["local_input_dir"], row["local_output_dir"])
                sync = sync_shared_outputs(run_id)
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE shared_matting_runs SET status = ?, synced_out = ?, updated_at = ? WHERE id = ?",
                        ("DONE", sync.get("count", 0), _now(), run_id),
                    )
                if row["chat_id"]:
                    send_text(row["chat_id"], _format_done(shared_matting_status(run_id)))
                return
            comfy = status.get("comfyui") or {}
            total = int(row["total"] or 0)
            completed = int(comfy.get("completed") or 0)
            failed = int(comfy.get("failed") or 0)
            if total and completed + failed >= total:
                sync = sync_shared_outputs(run_id)
                final_status = "DONE" if failed == 0 else "FAILED"
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE shared_matting_runs SET status = ?, synced_out = ?, updated_at = ? WHERE id = ?",
                        (final_status, sync.get("count", 0), _now(), run_id),
                    )
                if row["chat_id"]:
                    send_text(row["chat_id"], _format_done(shared_matting_status(run_id)))
                return
            time.sleep(int(row["notify_interval_seconds"] or 60))
        except Exception as exc:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE shared_matting_runs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                    ("FAILED", str(exc), _now(), run_id),
                )
            if row["chat_id"]:
                send_text(row["chat_id"], f"抠图任务失败：{run_id}\n{exc}")
            return


def _fake_complete_outputs(local_input_dir: str, local_output_dir: str) -> None:
    local_input = Path(local_input_dir)
    local_output = Path(local_output_dir)
    local_output.mkdir(parents=True, exist_ok=True)
    for src in local_input.iterdir():
        if src.is_file() and src.suffix.lower() in IMAGE_EXTS:
            shutil.copy2(src, local_output / src.name)


def _format_progress(status: dict[str, Any]) -> str:
    comfy = status.get("comfyui") or {}
    return (
        f"抠图进度：{status.get('run_id')}\n"
        f"{comfy.get('completed', 0)}/{status.get('total', 0)} 张，失败 {comfy.get('failed', 0)}\n"
        f"输入：{status.get('shared_input_dir')}\n"
        f"输出：{status.get('shared_output_dir')}"
    )


def _format_done(status: dict[str, Any]) -> str:
    return (
        f"抠图任务完成：{status.get('run_id')}\n"
        f"已同步输出：{status.get('synced_out', 0)} 个文件\n"
        f"输出目录：{status.get('shared_output_dir')}"
    )


def _get_run(run_id: str | None) -> Any:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        if run_id:
            return conn.execute("SELECT * FROM shared_matting_runs WHERE id = ?", (run_id,)).fetchone()
        return conn.execute("SELECT * FROM shared_matting_runs ORDER BY created_at DESC LIMIT 1").fetchone()

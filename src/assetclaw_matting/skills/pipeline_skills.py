from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.frame_skills import default_automation_paths
from assetclaw_matting.skills.security import validate_path


_WORKER_RUNS: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "PIPE_" + uuid.uuid4().hex[:12].upper()


def run_preview(
    input_dir: str | None = None,
    frame_output_dir: str | None = None,
    matte_output_dir: str | None = None,
    smooth_output_dir: str | None = None,
    workflow_path: str | None = None,
    fps: int = 24,
    max_frames: int = 24,
    diff_threshold: float = 0.2,
) -> dict[str, Any]:
    paths = _resolve_pipeline_paths(input_dir, frame_output_dir, matte_output_dir, smooth_output_dir)
    return {
        "ok": True,
        **paths,
        "workflow_path": workflow_path,
        "steps": ["1. 飞书表格下载视频并抽帧", "2. ComfyUI 批量抠图", "3. Cherry 帧序列平滑/缩放/锐化"],
        "frame": {"fps": int(fps), "max_frames": int(max_frames), "diff_threshold": float(diff_threshold)},
    }


def run_start(
    input_dir: str | None = None,
    frame_output_dir: str | None = None,
    matte_output_dir: str | None = None,
    smooth_output_dir: str | None = None,
    workflow_path: str | None = None,
    fps: int = 24,
    max_frames: int = 24,
    diff_threshold: float = 0.2,
    notify_interval_seconds: int = 60,
) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.runtime_context import get_runtime_context

    preview = run_preview(input_dir, frame_output_dir, matte_output_dir, smooth_output_dir, workflow_path, fps, max_frames, diff_threshold)
    run_id = _run_id()
    ctx = get_runtime_context()
    options = {
        "fps": int(fps),
        "max_frames": int(max_frames),
        "diff_threshold": float(diff_threshold),
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds), 3600)),
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "archived": False,
    }
    created = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs
            (id, status, input_dir, frame_output_dir, matte_output_dir, smooth_output_dir, workflow_path,
             frame_run_id, comfyui_run_id, cherry_run_id, current_step, options_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "RUNNING",
                preview["input_dir"],
                preview["frame_output_dir"],
                preview["matte_output_dir"],
                preview["smooth_output_dir"],
                workflow_path or "",
                "",
                "",
                "",
                "frame",
                json.dumps(options, ensure_ascii=False),
                "",
                created,
                created,
            ),
        )
    _notify(run_id, "动画自动化流程已启动：抽帧 -> 抠图 -> 平滑")
    _start_worker(run_id)
    return {"ok": True, "run_id": run_id, "status": "RUNNING", **preview}


def run_status(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "pipeline run not found"}
    payload = {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "current_step": row["current_step"],
        "workspace_root": _workspace_root_from_paths(row["input_dir"], row["frame_output_dir"], row["matte_output_dir"], row["smooth_output_dir"]),
        "input_dir": row["input_dir"],
        "frame_output_dir": row["frame_output_dir"],
        "matte_output_dir": row["matte_output_dir"],
        "smooth_output_dir": row["smooth_output_dir"],
        "workflow_path": row["workflow_path"],
        "frame_run_id": row["frame_run_id"],
        "comfyui_run_id": row["comfyui_run_id"],
        "cherry_run_id": row["cherry_run_id"],
        "error": row["error"] or "",
    }
    if row["frame_run_id"]:
        from assetclaw_matting.skills.frame_skills import run_status as frame_status

        payload["frame"] = frame_status(row["frame_run_id"])
    if row["comfyui_run_id"]:
        from assetclaw_matting.skills.comfyui_skills import run_status as comfy_status

        payload["comfyui"] = comfy_status(row["comfyui_run_id"], include_gpu=True)
    if row["cherry_run_id"]:
        from assetclaw_matting.skills.cherry_skills import run_status as cherry_status

        payload["cherry"] = cherry_status(row["cherry_run_id"], include_gpu=True)
    payload["detail_lines"] = _detail_lines(payload)
    return payload


def run_list(limit: int = 10, include_finished: bool = False) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute("SELECT id, status FROM pipeline_runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 50)),)).fetchall()
    items = []
    for row in rows:
        if row["status"] in {"DONE", "FAILED", "CANCELED"} and not include_finished:
            continue
        items.append(run_status(row["id"]))
    return {"ok": True, "count": len(items), "items": items}


def run_cancel(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "pipeline run not found"}
    if row["frame_run_id"]:
        from assetclaw_matting.skills.frame_skills import run_cancel

        run_cancel(row["frame_run_id"])
    if row["comfyui_run_id"]:
        from assetclaw_matting.skills.comfyui_skills import run_cancel

        run_cancel(row["comfyui_run_id"], interrupt_current=True)
    if row["cherry_run_id"]:
        from assetclaw_matting.skills.cherry_skills import run_cancel

        run_cancel(row["cherry_run_id"])
    _set_status(row["id"], "CANCELED")
    return {"ok": True, "run_id": row["id"], "status": "CANCELED"}


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    preview = run_preview(**arguments)
    lines = [
        "请确认是否开始动画自动化流程：",
        "步骤：抽帧 -> ComfyUI 抠图 -> Cherry 平滑",
        f"工作区：{preview['workspace_root']}",
        f"视频下载：{preview['input_dir']}",
        f"抽帧输出：{preview['frame_output_dir']}",
        f"抠图输出：{preview['matte_output_dir']}",
        f"平滑输出：{preview['smooth_output_dir']}",
        f"fps：{preview['frame']['fps']}，最多帧数：{preview['frame']['max_frames']}，相似阈值：{preview['frame']['diff_threshold']}",
        f"回复：确认执行 {confirmation_id}",
    ]
    return "\n".join(lines)


def _resolve_pipeline_paths(
    input_dir: str | None,
    frame_output_dir: str | None,
    matte_output_dir: str | None,
    smooth_output_dir: str | None,
) -> dict[str, str]:
    defaults = default_automation_paths()
    return {
        "workspace_root": str(validate_path(defaults["workspace_root"], must_exist=False)),
        "input_dir": str(validate_path(input_dir or defaults["video_dir"], must_exist=False)),
        "frame_output_dir": str(validate_path(frame_output_dir or defaults["frame_dir"], must_exist=False)),
        "matte_output_dir": str(validate_path(matte_output_dir or defaults["matte_dir"], must_exist=False)),
        "smooth_output_dir": str(validate_path(smooth_output_dir or defaults["smooth_dir"], must_exist=False)),
    }


def _workspace_root_from_paths(*paths: str) -> str:
    for raw in paths:
        path = Path(str(raw or ""))
        if path.name.lower() in {"videos", "frames", "matte", "smooth"}:
            return str(path.parent)
    return ""


def _start_worker(run_id: str) -> None:
    if run_id in _WORKER_RUNS:
        return
    _WORKER_RUNS.add(run_id)
    threading.Thread(target=_worker, args=(run_id,), daemon=True).start()


def _worker(run_id: str) -> None:
    try:
        row = _get_run(run_id)
        if not row:
            return
        opts = json.loads(row["options_json"] or "{}")
        token = None
        if opts.get("chat_id"):
            from assetclaw_matting.runtime_context import set_runtime_context

            token = set_runtime_context(channel="feishu", chat_id=opts["chat_id"])
        from assetclaw_matting.skills.frame_skills import run_start as frame_start, run_status as frame_status

        _notify(run_id, "动画自动化流程：开始飞书下载和抽帧")
        frame = frame_start(download_dir=row["input_dir"], export_dir=row["frame_output_dir"], fps=opts["fps"], max_frames=opts.get("max_frames", 24), diff_threshold=opts["diff_threshold"], notify_interval_seconds=opts["notify_interval_seconds"])
        _update_ids(run_id, frame_run_id=frame["run_id"], current_step="frame")
        if not _wait_until_done(lambda: frame_status(frame["run_id"]), run_id):
            return
        status = frame_status(frame["run_id"])
        if status.get("status") != "DONE":
            _fail(run_id, f"抽帧失败：{status.get('error') or status.get('status')}")
            return
        if not any(Path(row["frame_output_dir"]).rglob("*.png")):
            _fail(run_id, "抽帧没有产出图片。请确认飞书表格里有“动画”视频附件，并且角色/情绪父子记录对应正确。")
            return

        from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start, run_status as comfy_status

        _notify(run_id, f"动画自动化流程：抽帧完成，开始 ComfyUI 抠图\n已登记视频：{status.get('manifest_count', 0)} 条")
        comfy = comfy_start(
            workflow_path=row["workflow_path"] or None,
            input_dir=row["frame_output_dir"],
            output_dir=row["matte_output_dir"],
            recursive=True,
            preserve_structure=True,
            max_images=50000,
            skip_existing=True,
            notify_interval_seconds=opts["notify_interval_seconds"],
        )
        _update_ids(run_id, comfyui_run_id=comfy["run_id"], current_step="comfyui")
        if not _wait_until_done(lambda: comfy_status(comfy["run_id"], include_gpu=False), run_id):
            return
        status = comfy_status(comfy["run_id"], include_gpu=False)
        if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
            _fail(run_id, f"抠图失败：{status.get('error') or status.get('status')}")
            return

        from assetclaw_matting.skills.cherry_skills import run_start as cherry_start, run_status as cherry_status

        detail = status.get("last_completed_detail") or {}
        suffix = f"\n最后抠图：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}" if detail else ""
        _notify(run_id, f"动画自动化流程：抠图完成，开始 Cherry 平滑{suffix}")
        cherry = cherry_start(
            input_dir=row["matte_output_dir"],
            output_dir=row["smooth_output_dir"],
            recursive=True,
            max_images=50000,
            skip_existing=True,
            notify_interval_seconds=opts["notify_interval_seconds"],
        )
        _update_ids(run_id, cherry_run_id=cherry["run_id"], current_step="cherry")
        if not _wait_until_done(lambda: cherry_status(cherry["run_id"], include_gpu=False), run_id):
            return
        status = cherry_status(cherry["run_id"], include_gpu=False)
        if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
            _fail(run_id, f"平滑失败：{status.get('error') or status.get('status')}")
            return
        _set_status(run_id, "DONE")
        _notify(run_id, f"动画自动化流程完成：{run_id}\n最终输出：{row['smooth_output_dir']}")
    except Exception as exc:
        _fail(run_id, str(exc))
    finally:
        if "token" in locals() and token is not None:
            from assetclaw_matting.runtime_context import reset_runtime_context

            reset_runtime_context(token)
        _WORKER_RUNS.discard(run_id)


def _detail_lines(payload: dict[str, Any]) -> list[str]:
    lines = []
    step = payload.get("current_step")
    if step == "frame":
        frame = payload.get("frame") or {}
        current = frame.get("current_item") or {}
        if current:
            lines.append(f"当前位置：抽帧 {current.get('role')}/{current.get('emotion')}")
        if frame.get("last_log"):
            lines.append(f"抽帧日志：{frame.get('last_log')}")
    elif step == "comfyui":
        detail = (payload.get("comfyui") or {}).get("last_completed_detail") or {}
        if detail:
            lines.append(f"当前位置：抠图刚完成 {detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    elif step == "cherry":
        detail = (payload.get("cherry") or {}).get("last_completed_detail") or {}
        if detail:
            lines.append(f"当前位置：平滑刚完成 {detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    return lines


def _wait_until_done(fn, pipeline_id: str) -> bool:
    while True:
        row = _get_run(pipeline_id)
        if not row or row["status"] == "CANCELED":
            return False
        status = fn()
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return True
        time.sleep(5)


def _get_run(run_id: str | None = None):
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        if run_id:
            return conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        row = conn.execute("SELECT * FROM pipeline_runs WHERE status = 'RUNNING' ORDER BY created_at DESC LIMIT 1").fetchone()
        return row or conn.execute("SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def _set_status(run_id: str, status: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE pipeline_runs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), run_id))


def _update_ids(run_id: str, **updates: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    clauses = ["updated_at = ?"]
    values: list[Any] = [_now()]
    for key, value in updates.items():
        clauses.append(f"{key} = ?")
        values.append(value)
    values.append(run_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE pipeline_runs SET {', '.join(clauses)} WHERE id = ?", values)


def _fail(run_id: str, error: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE pipeline_runs SET status = ?, error = ?, updated_at = ? WHERE id = ?", ("FAILED", error, _now(), run_id))
    _notify(run_id, f"动画自动化流程失败：{error}")


def _notify(run_id: str, text: str) -> None:
    row = _get_run(run_id)
    if not row:
        return
    options = json.loads(row["options_json"] or "{}")
    chat_id = options.get("chat_id")
    if not chat_id:
        return
    from assetclaw_matting.services.notification_service import send_text

    send_text(chat_id, text)

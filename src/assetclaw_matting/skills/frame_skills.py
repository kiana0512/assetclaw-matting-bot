from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.security import validate_path


_WORKER_RUNS: set[str] = set()
_MONITORING_RUNS: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "FRAME_" + uuid.uuid4().hex[:12].upper()


def default_automation_paths(day: str | None = None) -> dict[str, str]:
    label = day or datetime.now().strftime("%Y-%m-%d")
    root = Path("E:/animation_automation") / label
    return {
        "workspace_root": str(root),
        "video_dir": str(root / "videos"),
        "frame_dir": str(root / "frames"),
        "matte_dir": str(root / "matte"),
        "smooth_dir": str(root / "smooth"),
    }


def info() -> dict[str, Any]:
    tool = _tool_dir()
    cfg = _load_base_config()
    defaults = default_automation_paths()
    return {
        "ok": True,
        "name": "feishu_frame_tool",
        "tool_dir": str(tool),
        "exists": tool.exists(),
        "config_path": str(tool / "config.json"),
        "fps": int(cfg.get("framepacker", {}).get("fps", 24)),
        "max_frames": int(cfg.get("framepacker", {}).get("max_frames", 24) or 0),
        "diff_threshold": float(cfg.get("dedup", {}).get("diff_threshold", 0.2)),
        "workspace_root": defaults["workspace_root"],
        "download_dir": defaults["video_dir"],
        "export_dir": defaults["frame_dir"],
        "selection": "all_records_with_animation",
    }


def run_preview(
    download_dir: str | None = None,
    export_dir: str | None = None,
    fps: int | None = None,
    max_frames: int | None = None,
    diff_threshold: float | None = None,
) -> dict[str, Any]:
    defaults = default_automation_paths()
    cfg = _build_config(download_dir=download_dir, export_dir=export_dir, fps=fps, max_frames=max_frames, diff_threshold=diff_threshold)
    return {
        "ok": True,
        "workspace_root": defaults["workspace_root"],
        "download_dir": cfg["paths"]["download_dir"],
        "export_dir": cfg["paths"]["export_dir"],
        "fps": cfg["framepacker"]["fps"],
        "max_frames": cfg["framepacker"].get("max_frames", 24),
        "dedup_enabled": cfg.get("dedup", {}).get("enabled", True),
        "diff_threshold": cfg.get("dedup", {}).get("diff_threshold", 0.2),
        "selection": "all_records_with_animation",
    }


def run_start(
    download_dir: str | None = None,
    export_dir: str | None = None,
    fps: int | None = None,
    max_frames: int | None = None,
    diff_threshold: float | None = None,
    notify_interval_seconds: int = 60,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.runtime_context import get_runtime_context

    cfg = _build_config(download_dir=download_dir, export_dir=export_dir, fps=fps, max_frames=max_frames, diff_threshold=diff_threshold)
    run_id = _run_id()
    run_dir = Path(settings.storage_dir) / "frame_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = run_dir / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    created = _now()
    ctx = get_runtime_context()
    options = {
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds), 3600)),
        "logs": [],
        "archived": False,
    }
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO frame_runs
            (id, status, config_path, download_dir, export_dir, total_records, processed_records, fps, diff_threshold, options_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "RUNNING",
                str(cfg_path),
                cfg["paths"]["download_dir"],
                cfg["paths"]["export_dir"],
                0,
                0,
                int(cfg["framepacker"]["fps"]),
                float(cfg.get("dedup", {}).get("diff_threshold", 0.2)),
                json.dumps(options, ensure_ascii=False),
                "",
                created,
                created,
            ),
        )
    if options.get("chat_id"):
        _notify(run_id, f"抽帧任务已启动\n下载：{cfg['paths']['download_dir']}\n导出：{cfg['paths']['export_dir']}")
        _start_progress_monitor(run_id)
    _start_worker(run_id)
    return {"ok": True, "run_id": run_id, "status": "RUNNING", "download_dir": cfg["paths"]["download_dir"], "export_dir": cfg["paths"]["export_dir"], "fps": cfg["framepacker"]["fps"], "max_frames": cfg["framepacker"].get("max_frames", 24), "diff_threshold": cfg.get("dedup", {}).get("diff_threshold", 0.2)}


def run_status(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "frame run not found"}
    total = int(row["total_records"] or 0)
    done = int(row["processed_records"] or 0)
    options = json.loads(row["options_json"] or "{}")
    logs = options.get("logs") or []
    manifest = _load_manifest(Path(row["export_dir"]))
    return {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "download_dir": row["download_dir"],
        "export_dir": row["export_dir"],
        "total_records": total,
        "processed_records": done,
        "progress_percent": round(done * 100 / total, 1) if total else 0,
        "fps": row["fps"],
        "diff_threshold": row["diff_threshold"],
        "last_log": logs[-1]["message"] if logs else "",
        "current_item": _current_item_from_logs(logs),
        "manifest_count": len(manifest),
        "items": manifest[:20],
        "error": row["error"] or "",
    }


def run_list(limit: int = 10, include_finished: bool = False, include_archived: bool = False) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM frame_runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 50)),)).fetchall()
    items = []
    for row in rows:
        options = json.loads(row["options_json"] or "{}")
        if options.get("archived") and not include_archived:
            continue
        if row["status"] in {"DONE", "FAILED", "CANCELED"} and not include_finished:
            continue
        items.append(run_status(row["id"]))
    return {"ok": True, "count": len(items), "items": items}


def run_cancel(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "frame run not found"}
    _set_status(row["id"], "CANCELED")
    _notify(row["id"], f"抽帧任务已终止：{row['id']}")
    return {"ok": True, "run_id": row["id"], "status": "CANCELED"}


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    try:
        preview = run_preview(**arguments)
        lines = [
            "请确认是否开始飞书抽帧任务：",
            f"下载目录：{preview['download_dir']}",
            f"抽帧输出：{preview['export_dir']}",
            f"fps：{preview['fps']}，最多帧数：{preview.get('max_frames') or '不限'}，相似阈值：{preview['diff_threshold']}",
            f"处理状态：{preview['status_from']} -> {preview['status_to']}",
            f"回复：确认执行 {confirmation_id}",
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"需要确认：frame.run_start\n预检查失败：{exc}\n回复：确认执行 {confirmation_id}"


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
        python = _python_path()
        script = Path("E:/assetclaw-matting-bot/scripts/frame_tool_worker.py")
        proc = subprocess.Popen(
            [str(python), str(script), row["config_path"], str(_tool_dir())],
            cwd=str(_tool_dir()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            latest = _get_run(run_id)
            if latest and latest["status"] == "CANCELED":
                proc.terminate()
                return
            _handle_log(run_id, line.strip())
        stderr = proc.stderr.read() if proc.stderr is not None else ""
        rc = proc.wait()
        latest = _get_run(run_id)
        if latest and latest["status"] == "CANCELED":
            return
        if rc != 0:
            _save_error(run_id, stderr.strip() or f"frame worker exited with {rc}")
            _set_status(run_id, "FAILED")
            _notify(run_id, f"抽帧任务异常：{stderr.strip() or rc}")
            return
        _set_status(run_id, "DONE")
    except Exception as exc:
        _save_error(run_id, str(exc))
        _set_status(run_id, "FAILED")
    finally:
        _WORKER_RUNS.discard(run_id)


def _handle_log(run_id: str, raw: str) -> None:
    if not raw:
        return
    try:
        event = json.loads(raw)
        message = str(event.get("message") or "")
    except json.JSONDecodeError:
        message = raw
    updates: dict[str, Any] = {}
    match = re.search(r"发现\s+(\d+)\s+条有动画附件的记录", message)
    if match:
        updates["total_records"] = int(match.group(1))
    if message.startswith("记录完成："):
        row = _get_run(run_id)
        updates["processed_records"] = int(row["processed_records"] or 0) + 1 if row else 1
    _append_log(run_id, message, updates)


def _current_item_from_logs(logs: list[dict[str, Any]]) -> dict[str, str]:
    for item in reversed(logs):
        message = str(item.get("message") or "")
        match = re.search(r"处理记录\s+\S+（(.+?)）", message)
        if not match:
            match = re.search(r"(?:下载视频|本地抽帧|抽帧记录)：(.+?)(?:\s*->|\s*/|\s*$)", message)
        if not match:
            continue
        label = match.group(1).strip()
        role, emotion = _split_label(label)
        return {"label": label, "role": role, "emotion": emotion}
    return {}


def _split_label(label: str) -> tuple[str, str]:
    clean = label.split("（", 1)[0].strip()
    parts = [part.strip() for part in clean.replace("\\", "/").split("/") if part.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", clean


def _load_manifest(export_dir: Path) -> list[dict[str, Any]]:
    path = export_dir / "_pipeline_manifest.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items")
    return items if isinstance(items, list) else []


def _append_log(run_id: str, message: str, updates: dict[str, Any] | None = None) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    row = _get_run(run_id)
    if not row:
        return
    options = json.loads(row["options_json"] or "{}")
    logs = options.get("logs") or []
    logs.append({"ts": _now(), "message": message})
    options["logs"] = logs[-80:]
    clauses = ["options_json = ?", "updated_at = ?"]
    values: list[Any] = [json.dumps(options, ensure_ascii=False), _now()]
    for key, value in (updates or {}).items():
        clauses.append(f"{key} = ?")
        values.append(value)
    values.append(run_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE frame_runs SET {', '.join(clauses)} WHERE id = ?", values)


def _start_progress_monitor(run_id: str) -> None:
    if run_id in _MONITORING_RUNS:
        return
    _MONITORING_RUNS.add(run_id)
    threading.Thread(target=_monitor, args=(run_id,), daemon=True).start()


def _monitor(run_id: str) -> None:
    try:
        last_done = -1
        while True:
            row = _get_run(run_id)
            if not row:
                return
            options = json.loads(row["options_json"] or "{}")
            if not options.get("chat_id"):
                return
            status = run_status(run_id)
            done = int(status.get("processed_records") or 0)
            if done != last_done or status.get("status") in {"DONE", "FAILED", "CANCELED"}:
                _notify(run_id, _format_status(status))
                last_done = done
            if status.get("status") in {"DONE", "FAILED", "CANCELED"}:
                return
            time.sleep(max(30, int(options.get("notify_interval_seconds") or 60)))
    finally:
        _MONITORING_RUNS.discard(run_id)


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


def _format_status(status: dict[str, Any]) -> str:
    lines = [
        f"抽帧进度：{status.get('processed_records')}/{status.get('total_records')} ({status.get('progress_percent')}%)",
        f"状态：{status.get('status')}",
    ]
    current = status.get("current_item") or {}
    if current:
        lines.append(f"当前记录：{current.get('role')}/{current.get('emotion')}")
    if status.get("last_log"):
        lines.append(f"最近日志：{status.get('last_log')}")
    lines.append(f"输出：{status.get('export_dir')}")
    return "\n".join(lines)


def _build_config(download_dir: str | None, export_dir: str | None, fps: int | None, max_frames: int | None, diff_threshold: float | None) -> dict[str, Any]:
    cfg = _load_base_config()
    defaults = default_automation_paths()
    if download_dir:
        cfg.setdefault("paths", {})["download_dir"] = str(validate_path(download_dir, must_exist=False))
    else:
        cfg.setdefault("paths", {})["download_dir"] = str(validate_path(defaults["video_dir"], must_exist=False))
    if export_dir:
        cfg.setdefault("paths", {})["export_dir"] = str(validate_path(export_dir, must_exist=False))
    else:
        cfg.setdefault("paths", {})["export_dir"] = str(validate_path(defaults["frame_dir"], must_exist=False))
    if fps is not None:
        cfg.setdefault("framepacker", {})["fps"] = int(fps)
    cfg.setdefault("framepacker", {})["max_frames"] = int(max_frames) if max_frames is not None else int(cfg.get("framepacker", {}).get("max_frames", 24) or 0)
    if diff_threshold is not None:
        cfg.setdefault("dedup", {})["diff_threshold"] = float(diff_threshold)
    return cfg


def _load_base_config() -> dict[str, Any]:
    return json.loads((_tool_dir() / "config.json").read_text(encoding="utf-8"))


def _project_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (_tool_dir() / p).resolve()


def _tool_dir() -> Path:
    from assetclaw_matting.config import settings

    return Path(settings.assetclaw_root) / "feishu_frame_tool"


def _python_path() -> Path:
    from assetclaw_matting.config import settings

    return Path(settings.comfyui_python_dir) / "python.exe"


def _get_run(run_id: str | None = None):
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        if run_id:
            return conn.execute("SELECT * FROM frame_runs WHERE id = ?", (run_id,)).fetchone()
        row = conn.execute("SELECT * FROM frame_runs WHERE status = 'RUNNING' ORDER BY created_at DESC LIMIT 1").fetchone()
        return row or conn.execute("SELECT * FROM frame_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def _set_status(run_id: str, status: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE frame_runs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), run_id))


def _save_error(run_id: str, error: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE frame_runs SET error = ?, updated_at = ? WHERE id = ?", (error, _now(), run_id))

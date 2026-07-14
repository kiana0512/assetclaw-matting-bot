from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.security import validate_path


_MODULE: ModuleType | None = None
_WORKER_RUNS: set[str] = set()
_AUTO_PROFILE_TOLERANCE = 0.01
_PROFILE_OVERRIDE_KEYS = {
    "use_denoise",
    "denoise_threshold",
    "denoise_radius",
    "use_shadow",
    "use_blur",
    "blur_radius",
    "blur_sigma",
    "use_resize1",
    "resize1_width",
    "resize1_height",
    "use_sharp1",
    "sharp1_amount",
    "sharp1_radius",
    "sharp1_threshold",
    "sharp1_shrink",
    "use_resize2",
    "resize2_width",
    "resize2_height",
    "use_sharp2",
    "sharp2_amount",
    "sharp2_radius",
    "sharp2_threshold",
    "sharp2_shrink",
    "use_resize",
    "resize_width",
    "resize_height",
    "use_sharpen",
    "sharpen_amount",
    "sharpen_radius",
    "sharpen_threshold",
    "sharpen_shrink",
}
_MONITORING_RUNS: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "CHERRY_" + uuid.uuid4().hex[:12].upper()


def info() -> dict[str, Any]:
    source = _tool_source_path()
    return {
        "ok": True,
        "name": "Cherry HTML 帧序列处理工具",
        "source_path": str(source),
        "exists": source.exists(),
        "engine": "headless_chrome_html",
        "steps": ["fringe", "hairinset", "feather(rect only)", "blur", "resize2"],
        "temporal_smooth": "controlled_by_html_default_off",
        "defaults": _default_options(),
        "presets": {"auto": preset_options("auto"), "full": preset_options("full"), "half": preset_options("half")},
    }


def run_preview(
    input_dir: str,
    output_dir: str,
    recursive: bool = True,
    max_images: int = 10000,
    **options: Any,
) -> dict[str, Any]:
    src = validate_path(input_dir, must_exist=True)
    dst = validate_path(output_dir, must_exist=False)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    files = _collect_images(src, recursive=recursive, max_images=max_images)
    groups = _group_sequences(src, files)
    return {
        "ok": True,
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
        "sequence_count": len(groups),
        "sample_inputs": [str(path.relative_to(src)) for path in files[:8]],
        "recursive": recursive,
        "preserve_structure": True,
        "options": _merge_options(options),
    }


def run_start(
    input_dir: str,
    output_dir: str,
    recursive: bool = True,
    max_images: int = 10000,
    skip_existing: bool = False,
    notify_interval_seconds: int = 60,
    **options: Any,
) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.runtime_context import get_runtime_context

    src = validate_path(input_dir, must_exist=True)
    dst = validate_path(output_dir, must_exist=False)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    files = _collect_images(src, recursive=recursive, max_images=max_images)
    if not files:
        raise ValueError("input_dir has no supported images")
    if skip_existing:
        files = [path for path in files if not _output_target(src, dst, path).exists()]
    dst.mkdir(parents=True, exist_ok=True)

    ctx = get_runtime_context()
    opts = _merge_options(options)
    opts.update(
        {
            "recursive": recursive,
            "skip_existing": skip_existing,
            "notify_interval_seconds": max(30, min(int(notify_interval_seconds), 3600)),
            "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
            "archived": False,
            "processed": [],
            "errors": [],
        }
    )
    run_id = _run_id()
    created_at = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cherry_runs
            (id, status, input_dir, output_dir, total, completed, failed, files_json, options_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "DONE" if not files else "RUNNING",
                str(src),
                str(dst),
                len(files),
                0,
                0,
                json.dumps([str(path) for path in files], ensure_ascii=False),
                json.dumps(opts, ensure_ascii=False),
                "",
                created_at,
                created_at,
            ),
        )
    if opts.get("chat_id") and files:
        _notify(run_id, f"Cherry 帧序列任务已启动：{len(files)} 张\n输入：{src}\n输出：{dst}")
        _start_progress_monitor(run_id)
    if files:
        _start_run_worker(run_id)
    return {
        "ok": True,
        "run_id": run_id,
        "status": "RUNNING",
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
        "sequence_count": len(_group_sequences(src, files)),
        "skip_existing": skip_existing,
        "options": opts,
    }


def run_status(run_id: str | None = None, include_gpu: bool = True) -> dict[str, Any]:
    from assetclaw_matting.skills.status_skills import gpu_status

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "cherry run not found"}
    options = json.loads(row["options_json"] or "{}")
    processed = options.get("processed") or []
    errors = options.get("errors") or []
    total = int(row["total"] or 0)
    completed = int(row["completed"] or len(processed))
    failed = int(row["failed"] or len(errors))
    elapsed = max(0.0, time.time() - datetime.fromisoformat(row["created_at"]).timestamp())
    eta_seconds = _eta(elapsed, completed, total)
    payload = {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "total": total,
        "completed": completed,
        "failed": failed,
        "running_or_pending": max(0, total - completed - failed),
        "progress_percent": round((completed + failed) * 100 / total, 1) if total else 0,
        "eta_seconds": eta_seconds,
        "last_completed": processed[-1]["rel_path"] if processed else "",
        "last_completed_detail": _path_detail(str(processed[-1]["rel_path"])) if processed else {},
        "error": row["error"] or "",
        "options": {k: v for k, v in options.items() if k not in {"processed", "errors", "chat_id"}},
    }
    if include_gpu:
        try:
            payload["gpu"] = gpu_status()
        except Exception:
            payload["gpu"] = {}
    return payload


def run_list(limit: int = 10, include_archived: bool = False, include_finished: bool = False) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, status, input_dir, output_dir, total, completed, failed, options_json, created_at, updated_at
            FROM cherry_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 50)),),
        ).fetchall()
    items = []
    for row in rows:
        options = json.loads(row["options_json"] or "{}")
        if options.get("archived") and not include_archived:
            continue
        if not include_finished and row["status"] in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            continue
        items.append(
            {
                "run_id": row["id"],
                "status": row["status"],
                "input_dir": row["input_dir"],
                "output_dir": row["output_dir"],
                "total": int(row["total"] or 0),
                "completed": int(row["completed"] or 0),
                "failed": int(row["failed"] or 0),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return {"ok": True, "count": len(items), "items": items}


def run_cancel(run_id: str | None = None, notify: bool = True) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "cherry run not found"}
    _set_run_status(row["id"], "CANCELED")
    if notify:
        _notify(row["id"], f"Cherry 任务已终止：{row['id']}")
    return {"ok": True, "run_id": row["id"], "status": "CANCELED"}


def run_delete(run_id: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "cherry run not found"}
    if row["status"] not in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
        return {"ok": False, "error": "任务还在运行中。先终止或等它结束，再删除记录。", "run_id": row["id"], "status": row["status"]}
    options = json.loads(row["options_json"] or "{}")
    options["archived"] = True
    with get_connection() as conn:
        conn.execute(
            "UPDATE cherry_runs SET options_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(options, ensure_ascii=False), _now(), row["id"]),
        )
    return {"ok": True, "run_id": row["id"], "status": "ARCHIVED"}


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    try:
        preview = run_preview(
            input_dir=arguments.get("input_dir") or "E:\\output",
            output_dir=arguments.get("output_dir") or "E:\\cherry_output",
            recursive=bool(arguments.get("recursive", True)),
            max_images=int(arguments.get("max_images") or 10000),
            **{k: v for k, v in arguments.items() if k not in {"input_dir", "output_dir", "recursive", "max_images"}},
        )
        opts = preview.get("options") or {}
        steps = _steps_text(opts)
        lines = [
            "请确认是否开始 Cherry 帧序列处理：",
            f"输入：{preview.get('input_dir')}",
            f"输出：{preview.get('output_dir')}",
            f"图片：{preview.get('total')} 张，序列：{preview.get('sequence_count')} 组",
            f"处理：{steps}",
        ]
        samples = preview.get("sample_inputs") or []
        if samples:
            lines.append("示例：" + "、".join(samples[:3]))
        lines.append(f"回复：确认执行 {confirmation_id}")
        return "\n".join(lines)
    except Exception as exc:
        return f"需要确认：cherry.run_start\n预检查失败：{exc}\n回复：确认执行 {confirmation_id}"


def _run_worker(run_id: str) -> None:
    try:
        row = _get_run(run_id)
        if not row:
            return
        from assetclaw_matting.config import settings

        if settings.cherry_html_runner_enabled:
            _run_worker_html(run_id, row)
            return
        python = _cherry_python_path()
        if python.exists():
            _run_worker_subprocess(run_id, row, python)
            return
        src = Path(row["input_dir"])
        dst = Path(row["output_dir"])
        files = [Path(path) for path in json.loads(row["files_json"] or "[]")]
        options = json.loads(row["options_json"] or "{}")
        groups = _group_sequences(src, files)
        module = _load_cherry_module()
        processed = options.get("processed") or []
        errors = options.get("errors") or []
        done = {item.get("src_path") for item in processed}

        for group_files in groups:
            latest = _get_run(run_id)
            if not latest or latest["status"] == "CANCELED":
                return
            pending = [path for path in group_files if str(path) not in done]
            if not pending:
                continue
            for process_files in _compatible_batches(module, pending, bool(options.get("use_smooth"))):
                try:
                    batch_np = [module.decode(path.read_bytes()) for path in process_files]
                    np, torch = _processing_deps()
                    batch = torch.from_numpy(np.stack(batch_np)).float() / 255.0
                    batch_options = _options_for_batch_shape(options, int(batch.shape[1]), int(batch.shape[2]))
                    batch = _apply_cherry_pipeline(module, batch, batch_options)
                    out_np = (batch.detach().cpu().numpy().clip(0, 1) * 255).astype(np.uint8)
                    for index, image_path in enumerate(process_files):
                        target = _output_target(src, dst, image_path)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(module.encode(out_np[index]))
                        processed.append({"src_path": str(image_path), "dst_path": str(target), "rel_path": str(image_path.relative_to(src))})
                        options["processed"] = processed
                        _save_progress(run_id, completed=len(processed), failed=len(errors), options=options)
                except Exception as exc:
                    for image_path in process_files:
                        errors.append({"src_path": str(image_path), "rel_path": str(image_path.relative_to(src)), "error": str(exc)})
                    options["errors"] = errors
                    _save_progress(run_id, completed=len(processed), failed=len(errors), options=options, error=str(exc))
                    _notify(run_id, f"Cherry 处理出错：{process_files[0].parent}\n{exc}")
                    _set_run_status(run_id, "FAILED")
                    return
        final_status = "DONE_WITH_ERRORS" if errors else "DONE"
        _set_run_status(run_id, final_status)
    except Exception as exc:
        _save_progress(run_id, error=str(exc))
        _set_run_status(run_id, "FAILED")
        _notify(run_id, f"Cherry 任务异常：{exc}")
    finally:
        _WORKER_RUNS.discard(run_id)


def _run_worker_html(run_id: str, row: Any) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.services.cherry_python_fallback import run_cherry_python_fallback
    from assetclaw_matting.services.cherry_html_runner import run_cherry_html

    src = Path(row["input_dir"])
    dst = Path(row["output_dir"])
    files = [Path(path) for path in json.loads(row["files_json"] or "[]")]
    options = json.loads(row["options_json"] or "{}")
    processed = options.get("processed") or []
    errors = options.get("errors") or []
    done = {item.get("src_path") for item in processed}
    groups = _group_sequences(src, files)
    options.setdefault("engine", "headless_chrome_html")
    options["source_path"] = str(_tool_source_path())

    for group_files in groups:
        latest = _get_run(run_id)
        if not latest or latest["status"] == "CANCELED":
            return
        pending = [path for path in group_files if str(path) not in done]
        if not pending:
            continue
        try:
            result = run_cherry_html(
                _tool_source_path(),
                src,
                dst,
                pending,
                chrome_path=Path(settings.cherry_browser_path) if settings.cherry_browser_path else None,
                timeout_seconds=int(settings.cherry_html_timeout_seconds),
                storage_dir=Path(settings.storage_dir),
            )
            width, height = _parse_resize(result.resize)
            if width and height:
                options["resize_width"] = width
                options["resize_height"] = height
            options["inferred_profile"] = result.profile
            options["html_feather_enabled"] = result.feather_enabled
            options["html_steps"] = result.steps
            options.setdefault("html_runs", []).append(
                {
                    "input_dir": str(pending[0].parent),
                    "count": len(pending),
                    "profile": result.profile,
                    "resize": result.resize,
                    "feather_enabled": result.feather_enabled,
                    "steps": result.steps,
                }
            )
            for image_path in pending:
                target = _output_target(src, dst, image_path)
                if not target.exists():
                    raise FileNotFoundError(str(target))
                processed.append({"src_path": str(image_path), "dst_path": str(target), "rel_path": str(image_path.relative_to(src))})
            options["processed"] = processed
            _save_progress(run_id, completed=len(processed), failed=len(errors), options=options)
        except Exception as exc:
            html_error = str(exc)
            try:
                result = run_cherry_python_fallback(src, dst, pending)
                width, height = _parse_resize(result.resize)
                if width and height:
                    options["resize_width"] = width
                    options["resize_height"] = height
                options["inferred_profile"] = result.profile
                options["html_feather_enabled"] = result.feather_enabled
                options["html_steps"] = result.steps
                options["fallback_used"] = True
                options["fallback_reason"] = html_error
                options.setdefault("html_runs", []).append(
                    {
                        "input_dir": str(pending[0].parent),
                        "count": len(pending),
                        "profile": result.profile,
                        "resize": result.resize,
                        "feather_enabled": result.feather_enabled,
                        "steps": result.steps,
                        "fallback": "html_python_fallback",
                        "html_error": html_error,
                    }
                )
                for image_path in pending:
                    target = _output_target(src, dst, image_path)
                    if not target.exists():
                        raise FileNotFoundError(str(target))
                    processed.append({"src_path": str(image_path), "dst_path": str(target), "rel_path": str(image_path.relative_to(src))})
                options["processed"] = processed
                _save_progress(run_id, completed=len(processed), failed=len(errors), options=options, error=f"HTML fallback used: {html_error}")
            except Exception as fallback_exc:
                for image_path in pending:
                    errors.append({"src_path": str(image_path), "rel_path": str(image_path.relative_to(src)), "error": str(fallback_exc)})
                options["errors"] = errors
                options["fallback_reason"] = html_error
                _save_progress(run_id, completed=len(processed), failed=len(errors), options=options, error=str(fallback_exc))
                _set_run_status(run_id, "FAILED")
                _notify(run_id, f"Cherry 后处理失败：{pending[0].parent}\nHTML: {html_error}\nFallback: {fallback_exc}")
                return

    final_status = "DONE_WITH_ERRORS" if errors else "DONE"
    _set_run_status(run_id, final_status)


def _run_worker_subprocess(run_id: str, row: Any, python: Path) -> None:
    from assetclaw_matting.config import settings

    src = Path(row["input_dir"])
    dst = Path(row["output_dir"])
    files = [Path(path) for path in json.loads(row["files_json"] or "[]")]
    options = json.loads(row["options_json"] or "{}")
    processed = options.get("processed") or []
    errors = options.get("errors") or []
    run_dir = Path(settings.storage_dir) / "cherry_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "source_path": str(_legacy_tool_source_path()),
                "input_dir": str(src),
                "output_dir": str(dst),
                "files": [str(path) for path in files],
                "options": options,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = Path(settings.assetclaw_root) / "scripts" / "cherry_batch_worker.py"
    proc = subprocess.Popen(
        [str(python), str(script), str(config_path)],
        cwd=str(settings.assetclaw_root),
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
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "done":
            processed.append({"src_path": event.get("src_path"), "dst_path": event.get("dst_path"), "rel_path": event.get("rel_path")})
            options["processed"] = processed
            _save_progress(run_id, completed=int(event.get("completed") or len(processed)), failed=int(event.get("failed") or len(errors)), options=options)
        elif event.get("event") == "error":
            errors.append({"src_path": event.get("src_path"), "rel_path": event.get("rel_path"), "error": event.get("error")})
            options["errors"] = errors
            _save_progress(run_id, completed=int(event.get("completed") or len(processed)), failed=int(event.get("failed") or len(errors)), options=options, error=str(event.get("error") or ""))
        elif event.get("event") == "finished":
            _save_progress(run_id, completed=int(event.get("completed") or len(processed)), failed=int(event.get("failed") or len(errors)), options=options)
    stderr = proc.stderr.read() if proc.stderr is not None else ""
    rc = proc.wait()
    latest = _get_run(run_id)
    if latest and latest["status"] == "CANCELED":
        return
    if rc != 0:
        message = stderr.strip() or f"Cherry worker exited with code {rc}"
        _save_progress(run_id, error=message)
        _set_run_status(run_id, "FAILED")
        _notify(run_id, f"Cherry 任务异常：{message}")
        return
    final_status = "DONE_WITH_ERRORS" if errors else "DONE"
    _set_run_status(run_id, final_status)


def _start_run_worker(run_id: str) -> None:
    if run_id in _WORKER_RUNS:
        return
    _WORKER_RUNS.add(run_id)
    threading.Thread(target=_run_worker, args=(run_id,), daemon=True).start()


def _start_progress_monitor(run_id: str) -> None:
    if run_id in _MONITORING_RUNS:
        return
    _MONITORING_RUNS.add(run_id)
    threading.Thread(target=_monitor_run, args=(run_id,), daemon=True).start()


def _monitor_run(run_id: str) -> None:
    try:
        last_completed = -1
        while True:
            row = _get_run(run_id)
            if not row:
                return
            options = json.loads(row["options_json"] or "{}")
            if not options.get("chat_id"):
                return
            status = run_status(run_id, include_gpu=True)
            completed = int(status.get("completed") or 0)
            if completed != last_completed or status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
                _notify(run_id, _format_progress_notification(status))
                last_completed = completed
            if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
                if status.get("status") in {"DONE", "DONE_WITH_ERRORS"}:
                    _notify(run_id, f"Cherry 帧序列任务完成：{status.get('completed', 0)}/{status.get('total', 0)} 张\n输出：{status.get('output_dir')}")
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


def _format_progress_notification(status: dict[str, Any]) -> str:
    lines = [
        f"Cherry 进度：{status.get('completed', 0)}/{status.get('total', 0)} ({status.get('progress_percent', 0)}%)",
        f"状态：{status.get('status')}",
    ]
    eta = status.get("eta_seconds")
    if isinstance(eta, int):
        lines.append(f"预计剩余：{_format_duration(eta)}")
    if status.get("last_completed"):
        lines.append(f"刚完成：{status.get('last_completed')}")
    detail = status.get("last_completed_detail") or {}
    if detail:
        lines.append(f"角色/情绪/帧：{detail.get('role')}/{detail.get('emotion')}/{detail.get('frame')}")
    gpu = status.get("gpu") or {}
    gpus = gpu.get("gpus") or []
    if gpus:
        first = gpus[0]
        lines.append(
            "GPU：显存 "
            f"{first.get('memory_used_mb')}/{first.get('memory_total_mb')} MB，"
            f"利用率 {first.get('utilization_gpu_percent')}%"
        )
    return "\n".join(lines)


def _path_detail(rel_path: str) -> dict[str, str]:
    parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
    if len(parts) >= 4 and parts[-2].lower().startswith("video_"):
        role = parts[-4]
        emotion = parts[-3]
    else:
        role = parts[-3] if len(parts) >= 3 else (parts[-2] if len(parts) >= 2 else "")
        emotion = parts[-2] if len(parts) >= 2 else ""
    frame = parts[-1] if parts else ""
    return {"role": role, "emotion": emotion, "frame": frame, "rel_path": rel_path}


def _get_run(run_id: str | None = None):
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        if run_id:
            return conn.execute("SELECT * FROM cherry_runs WHERE id = ?", (run_id,)).fetchone()
        row = conn.execute(
            """
            SELECT * FROM cherry_runs
            WHERE status IN ('RUNNING')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            return row
        return conn.execute("SELECT * FROM cherry_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def _set_run_status(run_id: str, status: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE cherry_runs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), run_id))


def _save_progress(
    run_id: str,
    completed: int | None = None,
    failed: int | None = None,
    options: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    updates = ["updated_at = ?"]
    values: list[Any] = [_now()]
    if completed is not None:
        updates.append("completed = ?")
        values.append(completed)
    if failed is not None:
        updates.append("failed = ?")
        values.append(failed)
    if options is not None:
        updates.append("options_json = ?")
        values.append(json.dumps(options, ensure_ascii=False))
    if error is not None:
        updates.append("error = ?")
        values.append(error)
    values.append(run_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE cherry_runs SET {', '.join(updates)} WHERE id = ?", values)


def _load_cherry_module() -> ModuleType:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    source = _legacy_tool_source_path()
    if not source.exists():
        raise FileNotFoundError(str(source))
    spec = importlib.util.spec_from_file_location("assetclaw_cherry_temporal_smooth", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Cherry tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def _processing_deps():
    try:
        import numpy as np
        import torch
    except ModuleNotFoundError as exc:
        raise RuntimeError("Cherry 处理依赖缺失，请先安装 Cherry_帧序列处理工具/requirements.txt 里的 numpy、torch、opencv-python、flask、Pillow") from exc
    return np, torch


def _tool_source_path() -> Path:
    from assetclaw_matting.config import settings

    return Path(settings.cherry_postprocess_html_path)


def _legacy_tool_source_path() -> Path:
    from assetclaw_matting.config import settings

    root = Path(settings.assetclaw_root)
    candidates = [
        root / "Cherry_后处理网页_源码_20260615_0658" / "web_temporal_smooth.py",
        root / "Cherry_帧序列处理工具_2" / "web_temporal_smooth.py",
        root / "Cherry_帧序列处理工具_1" / "web_temporal_smooth.py",
        root / "Cherry_帧序列处理工具" / "web_temporal_smooth.py",
    ]
    for source in candidates:
        if source.exists():
            return source
    return candidates[0]


def _cherry_python_path() -> Path:
    from assetclaw_matting.config import settings

    return Path(settings.comfyui_python_dir) / "python.exe"


def _collect_images(root: Path, recursive: bool, max_images: int) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [path for path in iterator if path.is_file() and path.suffix.lower() in IMAGE_EXTS]
    return sorted(files, key=lambda path: str(path.relative_to(root)).lower())[: max(1, min(max_images, 50000))]


def _group_sequences(root: Path, files: list[Path]) -> list[list[Path]]:
    groups: dict[Path, list[Path]] = defaultdict(list)
    for path in files:
        groups[path.parent.relative_to(root)].append(path)
    return [sorted(paths, key=lambda path: path.name.lower()) for _rel, paths in sorted(groups.items(), key=lambda item: str(item[0]).lower())]


def _compatible_batches(module: ModuleType, files: list[Path], require_same_shape: bool) -> list[list[Path]]:
    by_shape: dict[tuple[int, ...], list[Path]] = defaultdict(list)
    for path in files:
        shape = tuple(module.decode(path.read_bytes()).shape)
        by_shape[shape].append(path)
    if len(by_shape) <= 1:
        return [files]
    if require_same_shape:
        raise ValueError(f"同一序列内图片尺寸不一致：{files[0].parent}")
    return [paths for _shape, paths in sorted(by_shape.items(), key=lambda item: str(item[0]))]


def _output_target(src: Path, dst: Path, image_path: Path) -> Path:
    return dst / image_path.relative_to(src).with_suffix(".png")


def _parse_resize(value: str) -> tuple[int | None, int | None]:
    try:
        left, right = str(value).lower().split("x", 1)
        return int(left), int(right)
    except Exception:
        return None, None


def _default_options() -> dict[str, Any]:
    return preset_options("auto")


def preset_options(profile: str = "full", use_smooth: bool = False) -> dict[str, Any]:
    normalized = str(profile or "full").lower()
    auto_profile = normalized in {"auto", "adaptive", "size"}
    is_half = normalized in {"half", "emoji", "square"}
    width, height = (256, 256) if is_half else (384, 512)
    return {
        "engine": "headless_chrome_html",
        "source": "cherry-postprocess.html",
        "profile": "auto" if auto_profile else ("half" if is_half else "full"),
        "auto_profile_by_size": auto_profile,
        "use_denoise": True,
        "denoise_threshold": 1.0,
        "denoise_radius": 9,
        "use_shadow": not is_half,
        "shadow_gray_limit": 0.35,
        "shadow_protect_radius": -70,
        "shadow_alpha_boost": 1.0,
        "shadow_blur_radius": 2,
        "shadow_blur_sigma": 2.4,
        "use_blur": True,
        "blur_radius": 14,
        "blur_sigma": 12.0,
        "use_resize1": False,
        "resize1_width": width,
        "resize1_height": height,
        "use_sharp1": False,
        "sharp1_amount": 0.3,
        "sharp1_radius": 2,
        "sharp1_threshold": 0.02,
        "sharp1_shrink": 11,
        "use_resize2": True,
        "resize2_width": width,
        "resize2_height": height,
        "use_sharp2": False,
        "sharp2_amount": 0.3,
        "sharp2_radius": 2,
        "sharp2_threshold": 0.02,
        "sharp2_shrink": 11,
        "use_smooth": False,
        "smooth_window": 7,
        "smooth_sigma": 1.5,
        "min_alpha": 0.05,
        "sync_rgb": False,
        "ring_width": 10,
        "smooth_method": "html_default_off",
        "fill_gap": True,
        "bg_thresh": 0.02,
        "use_resize": True,
        "resize_width": width,
        "resize_height": height,
        "use_sharpen": False,
        "sharpen_amount": 0.3,
        "sharpen_radius": 2,
        "sharpen_threshold": 0.02,
        "sharpen_shrink": 11,
        "html_modules": ["fringe", "hairinset", *([] if is_half else ["feather"]), "blur", "resize2"],
        "html_feather_enabled": not is_half,
    }


def _merge_options(options: dict[str, Any]) -> dict[str, Any]:
    profile = options.get("profile") or options.get("preset")
    if profile is not None:
        merged = preset_options(str(profile), use_smooth=bool(options.get("use_smooth", False)))
    else:
        merged = _default_options()
    aliases = {
        "preset": "profile",
        "auto_size_profile": "auto_profile_by_size",
        "window_size": "smooth_window",
        "sigma": "smooth_sigma",
        "use_clean": "use_denoise",
        "use_denoise_alpha": "use_denoise",
        "use_shadowsep": "use_shadow",
        "denoise_thresh": "denoise_threshold",
        "dn_thresh": "denoise_threshold",
        "denoise_smooth_radius": "denoise_radius",
        "dn_radius": "denoise_radius",
        "use_item_shadow": "use_shadow",
        "sep_gray": "shadow_gray_limit",
        "sep_protect": "shadow_protect_radius",
        "sep_boost": "shadow_alpha_boost",
        "shadow_gray_upper": "shadow_gray_limit",
        "shadow_protect": "shadow_protect_radius",
        "shadow_boost": "shadow_alpha_boost",
        "resize_w": "resize_width",
        "resize_h": "resize_height",
        "use_resize_1": "use_resize1",
        "resize1_w": "resize1_width",
        "resize1_h": "resize1_height",
        "use_resize_2": "use_resize2",
        "resize2_w": "resize2_width",
        "resize2_h": "resize2_height",
        "use_sharp_1": "use_sharp1",
        "sharp1_thresh": "sharp1_threshold",
        "use_sharp_2": "use_sharp2",
        "sharp2_thresh": "sharp2_threshold",
        "sharp_amount": "sharpen_amount",
        "sharp_radius": "sharpen_radius",
        "sharp_thresh": "sharpen_threshold",
        "sharp_shrink": "sharpen_shrink",
    }
    for key, value in options.items():
        normalized = aliases.get(key, key)
        if normalized in merged and value is not None:
            merged[normalized] = value
    if "use_resize" in options:
        merged["use_resize1"] = bool(options.get("use_resize"))
        merged["use_resize2"] = bool(options.get("use_resize"))
    if "use_sharpen" in options:
        merged["use_sharp1"] = bool(options.get("use_sharpen"))
        merged["use_sharp2"] = bool(options.get("use_sharpen"))
    if "resize_width" in options:
        merged["resize2_width"] = int(options.get("resize_width"))
    if "resize_height" in options:
        merged["resize2_height"] = int(options.get("resize_height"))
    if "sharpen_amount" in options:
        merged["sharp2_amount"] = float(options.get("sharpen_amount"))
    if "sharpen_radius" in options:
        merged["sharp2_radius"] = int(options.get("sharpen_radius"))
    if "sharpen_threshold" in options:
        merged["sharp2_threshold"] = float(options.get("sharpen_threshold"))
    if "sharpen_shrink" in options:
        merged["sharp2_shrink"] = int(options.get("sharpen_shrink"))
    for key in (
        "use_denoise",
        "use_blur",
        "use_resize1",
        "use_sharp1",
        "use_shadow",
        "use_resize2",
        "use_sharp2",
        "use_smooth",
        "sync_rgb",
        "fill_gap",
        "use_resize",
        "use_sharpen",
        "auto_profile_by_size",
    ):
        merged[key] = bool(merged[key])
    for key in (
        "denoise_radius",
        "blur_radius",
        "resize1_width",
        "resize1_height",
        "sharp1_radius",
        "sharp1_shrink",
        "shadow_protect_radius",
        "shadow_blur_radius",
        "resize2_width",
        "resize2_height",
        "sharp2_radius",
        "sharp2_shrink",
        "smooth_window",
        "ring_width",
        "resize_width",
        "resize_height",
        "sharpen_radius",
        "sharpen_shrink",
    ):
        merged[key] = int(merged[key])
    for key in (
        "denoise_threshold",
        "blur_sigma",
        "sharp1_amount",
        "sharp1_threshold",
        "shadow_gray_limit",
        "shadow_alpha_boost",
        "shadow_blur_sigma",
        "sharp2_amount",
        "sharp2_threshold",
        "smooth_sigma",
        "min_alpha",
        "bg_thresh",
        "sharpen_amount",
        "sharpen_threshold",
    ):
        merged[key] = float(merged[key])
    return merged


def _infer_profile_from_shape(height: int, width: int) -> str:
    if height <= 0 or width <= 0:
        return "full"
    ratio = float(width) / float(height)
    return "half" if abs(ratio - 1.0) <= _AUTO_PROFILE_TOLERANCE else "full"


def _options_for_batch_shape(options: dict[str, Any], height: int, width: int) -> dict[str, Any]:
    if not options.get("auto_profile_by_size"):
        return options
    inferred = _infer_profile_from_shape(height, width)
    preset = preset_options(inferred, use_smooth=bool(options.get("use_smooth", False)))
    adjusted = dict(options)
    for key in _PROFILE_OVERRIDE_KEYS:
        adjusted[key] = preset[key]
    adjusted["profile"] = "auto"
    adjusted["inferred_profile"] = inferred
    adjusted["auto_profile_by_size"] = True
    return adjusted


def _apply_cherry_pipeline(module: ModuleType, batch: Any, options: dict[str, Any]) -> Any:
    shadow_source = batch
    if options.get("use_denoise"):
        batch = module.alpha_denoise(
            batch,
            float(options.get("denoise_threshold", 0.06)),
            int(options.get("denoise_radius", 0)),
        )
    if options.get("use_shadow"):
        batch = _shadow_separate_char(module, batch, shadow_source, options)
    if options.get("use_blur") and hasattr(module, "blur_under_composite"):
        batch = module.blur_under_composite(batch, int(options.get("blur_radius", 1)), float(options.get("blur_sigma", 10.0)))
    if options.get("use_resize1"):
        batch = module.ps_bicubic_sharper(batch, int(options.get("resize1_width", 768)), int(options.get("resize1_height", 1024)))
    if options.get("use_sharp1"):
        batch = _sharpen(
            module,
            batch,
            float(options.get("sharp1_amount", 1.0)),
            int(options.get("sharp1_radius", 2)),
            float(options.get("sharp1_threshold", 0.02)),
            int(options.get("sharp1_shrink", 0)),
            float(options.get("min_alpha", 0.05)),
        )
    if options.get("use_resize2"):
        batch = module.ps_bicubic_sharper(batch, int(options.get("resize2_width", 384)), int(options.get("resize2_height", 512)))
    if options.get("use_sharp2"):
        batch = _sharpen(
            module,
            batch,
            float(options.get("sharp2_amount", 1.0)),
            int(options.get("sharp2_radius", 2)),
            float(options.get("sharp2_threshold", 0.02)),
            int(options.get("sharp2_shrink", 5)),
            float(options.get("min_alpha", 0.05)),
        )
    if options.get("use_smooth"):
        batch = _temporal_smooth(module, batch, options)
    return batch


def _shadow_separate_char(module: ModuleType, batch: Any, shadow_source: Any, options: dict[str, Any]) -> Any:
    separator = getattr(module, "shadow_separate_v5", None) or getattr(module, "shadow_separate", None)
    if separator is None:
        shadow_clean = module.alpha_denoise(shadow_source, 0.01, 0)
        return _merge_item_shadow(batch, shadow_clean, options)

    np, torch = _processing_deps()
    branch = module.alpha_denoise(shadow_source.clone(), 0.01, 0)
    item_branch = module.alpha_denoise(
        shadow_source.clone(),
        float(options.get("denoise_threshold", 0.06)),
        int(options.get("denoise_radius", 0)),
    )
    if item_branch.shape[1:3] != branch.shape[1:3]:
        item_branch = module.ps_bicubic_sharper(item_branch, branch.shape[2], branch.shape[1])
    item_a = item_branch[..., 3:4].clamp(0.0, 1.0)
    item_rgb_ref = (item_branch[..., :3] * item_a + (1.0 - item_a)).clamp(0.0, 1.0)
    char_branch, shadow_batch = separator(
        branch,
        float(options.get("shadow_gray_limit", 0.35)),
        int(options.get("shadow_protect_radius", -70)),
        0.1,
        float(options.get("shadow_alpha_boost", 1.0)),
        int(options.get("shadow_blur_radius", 2)),
        float(options.get("shadow_blur_sigma", 2.4)),
        item_alpha=item_branch[..., 3],
        item_rgb=item_rgb_ref,
    )
    if char_branch.shape[1:3] != batch.shape[1:3]:
        char_branch = module.ps_bicubic_sharper(char_branch, batch.shape[2], batch.shape[1])
    if shadow_batch.shape[1:3] != batch.shape[1:3]:
        shadow_batch = module.ps_bicubic_sharper(shadow_batch, batch.shape[2], batch.shape[1])
    if item_branch.shape[1:3] != shadow_batch.shape[1:3]:
        item_branch = module.ps_bicubic_sharper(item_branch, shadow_batch.shape[2], shadow_batch.shape[1])

    cv2 = getattr(module, "cv2", None)
    if cv2 is None:
        return char_branch
    solid_now = (item_branch[..., 3] > 0.01).cpu().numpy().astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    solid_now = np.stack([cv2.dilate(solid_now[i], kernel) for i in range(solid_now.shape[0])], axis=0)
    protect = torch.from_numpy(solid_now.astype(np.float32)).to(batch.device).unsqueeze(-1)
    new_a = torch.where(
        protect > 0.5,
        batch[..., 3:4],
        torch.minimum(
            torch.clamp(batch[..., 3:4] - shadow_batch[..., 3:4], 0.0, 1.0),
            char_branch[..., 3:4].clamp(0.0, 1.0),
        ),
    )
    return torch.cat([batch[..., :3], new_a], dim=-1)


def _sharpen(module: ModuleType, batch: Any, amount: float, radius: int, threshold: float, shrink: int, min_alpha: float) -> Any:
    args = [batch, amount, radius, threshold, shrink]
    if len(inspect.signature(module.sharpen).parameters) >= 6:
        args.append(min_alpha)
    return module.sharpen(*args)


def _merge_item_shadow(batch: Any, shadow_source: Any, options: dict[str, Any]) -> Any:
    if getattr(batch, "shape", None) is None or batch.shape[-1] < 4:
        return batch
    torch = __import__("torch")
    gray_limit = float(options.get("shadow_gray_limit", 0.35))
    protect_radius = int(options.get("shadow_protect_radius", -70))
    boost = float(options.get("shadow_alpha_boost", 1.0))

    src_rgb = shadow_source[..., :3].clamp(0.0, 1.0)
    src_a = shadow_source[..., 3:4].clamp(0.0, 1.0)
    gray = src_rgb.mean(dim=-1, keepdim=True)
    chroma = src_rgb.max(dim=-1, keepdim=True).values - src_rgb.min(dim=-1, keepdim=True).values

    _, h, w, _ = batch.shape
    yy = torch.linspace(0.0, 1.0, h, device=batch.device, dtype=batch.dtype).view(1, h, 1, 1)
    xx = torch.linspace(0.0, 1.0, w, device=batch.device, dtype=batch.dtype).view(1, 1, w, 1)
    foot_ellipse = (((xx - 0.5) / 0.46) ** 2 + ((yy - 0.82) / 0.24) ** 2) <= 1.0
    shadow_mask = (src_a > 0.01) & (gray <= gray_limit) & (chroma <= 0.12) & foot_ellipse

    if protect_radius:
        person = (src_a > float(options.get("min_alpha", 0.05))).permute(0, 3, 1, 2).float()
        radius = min(abs(protect_radius), max(1, min(h, w) // 3))
        kernel = radius * 2 + 1
        if protect_radius < 0:
            protected = -torch.nn.functional.max_pool2d(-person, kernel, stride=1, padding=radius)
        else:
            protected = torch.nn.functional.max_pool2d(person, kernel, stride=1, padding=radius)
        shadow_mask = shadow_mask & ~(protected.permute(0, 2, 3, 1) > 0.5)

    base_rgb = batch[..., :3].clamp(0.0, 1.0)
    base_a = batch[..., 3:4].clamp(0.0, 1.0)
    shadow_a = torch.where(shadow_mask, (src_a * boost).clamp(0.0, 1.0), torch.zeros_like(src_a))
    shadow_rgb = src_rgb * 0.75
    out_a = torch.maximum(base_a, shadow_a)
    out_rgb = torch.where(
        out_a > 1e-6,
        (base_rgb * base_a + shadow_rgb * shadow_a * (1.0 - base_a)).clamp(0.0, 1.0) / out_a.clamp(min=1e-6),
        base_rgb,
    )
    return torch.cat([out_rgb, out_a], dim=-1)


def _temporal_smooth(module: ModuleType, batch: Any, options: dict[str, Any]) -> Any:
    args = [
        batch,
        int(options.get("smooth_window", 5)),
        float(options.get("smooth_sigma", 1.0)),
        bool(options.get("sync_rgb", False)),
        float(options.get("min_alpha", 0.05)),
    ]
    parameter_count = len(inspect.signature(module.temporal_smooth).parameters)
    if parameter_count >= 6:
        args.append(int(options.get("ring_width", 25)))
    if parameter_count >= 7:
        args.append(str(options.get("smooth_method", "中值+高斯")))
    if parameter_count >= 8:
        args.append(bool(options.get("fill_gap", True)))
    if parameter_count >= 9:
        args.append(float(options.get("bg_thresh", 0.02)))
    return module.temporal_smooth(*args)


def _steps_text(options: dict[str, Any]) -> str:
    if options.get("engine") == "headless_chrome_html":
        modules = options.get("html_modules") or ["fringe", "hairinset", "feather", "blur", "resize2"]
        feather = "开" if options.get("html_feather_enabled") else "关"
        return f"HTML 默认预设，输出 {options.get('resize_width')}x{options.get('resize_height')}，feather {feather}，模块 {'/'.join(modules)}"
    steps = []
    if options.get("use_denoise"):
        steps.append(f"去噪 阈值{options.get('denoise_threshold')} 半径{options.get('denoise_radius')}")
    if options.get("use_shadow"):
        steps.append(
            f"阴影分离 灰度{options.get('shadow_gray_limit')} 保护{options.get('shadow_protect_radius')} 增强{options.get('shadow_alpha_boost')}"
        )
    if options.get("use_blur"):
        steps.append(f"模糊自叠加 半径{options.get('blur_radius')} 强度{options.get('blur_sigma')}")
    if options.get("use_resize1"):
        steps.append(f"缩小① {options.get('resize1_width')}x{options.get('resize1_height')}")
    if options.get("use_sharp1"):
        steps.append(f"锐化① 强度{options.get('sharp1_amount')}")
    if options.get("use_resize2"):
        steps.append(f"缩小② {options.get('resize2_width')}x{options.get('resize2_height')}")
    if options.get("use_sharp2"):
        steps.append(f"锐化② 强度{options.get('sharp2_amount')}")
    if options.get("use_smooth"):
        steps.append(f"时序平滑 窗口{options.get('smooth_window')} 强度{options.get('smooth_sigma')}")
    return "、".join(steps) or "无"


def _eta(elapsed: float, completed: int, total: int) -> int | None:
    if completed <= 0 or total <= completed:
        return None
    return int((elapsed / completed) * (total - completed))


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"

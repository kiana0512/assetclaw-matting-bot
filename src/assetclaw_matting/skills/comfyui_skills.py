from __future__ import annotations

import copy
import json
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assetclaw_matting.comfyui.output_resolver import resolve_first_output
from assetclaw_matting.comfyui.workflow_patch import inspect_workflow, patch_load_image, patch_node_input, prepare_api_prompt_for_run
from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.security import validate_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "COMFY_" + uuid.uuid4().hex[:12].upper()


def workflows_list(path: str | None = None, max_results: int = 50) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    items = []
    roots = [validate_path(path, must_exist=True)] if path else _default_workflow_roots()
    limit = max(1, min(max_results, 200))
    for item in _iter_json_files(roots, limit=limit):
        stat = item.stat()
        info = _safe_workflow_brief(item)
        items.append({"name": item.name, "path": str(item), "size": stat.st_size, "modified_at": stat.st_mtime, **info})
        if len(items) >= limit:
            break
    return {"ok": True, "path": "; ".join(str(root) for root in roots), "count": len(items), "items": items}


def workflow_info(path: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    target = _resolve_workflow_path(path or _selected_workflow_path() or str(settings.comfyui_workflow_path))
    if target.suffix.lower() != ".json":
        raise ValueError("workflow path must be a json file")
    workflow = json.loads(target.read_text(encoding="utf-8"))
    info = inspect_workflow(workflow)
    return {"ok": True, "path": str(target), **info}


def workflow_select(path: str) -> dict[str, Any]:
    from assetclaw_matting.db.repos import upsert_memory_note
    from assetclaw_matting.runtime_context import get_runtime_context

    target = _resolve_workflow_path(path)
    if target.suffix.lower() != ".json":
        raise ValueError("workflow path must be a json file")
    workflow = json.loads(target.read_text(encoding="utf-8"))
    info = inspect_workflow(workflow)
    ctx = get_runtime_context()
    scope = ctx.get("conversation_id") or "global"
    upsert_memory_note(scope, "selected_comfyui_workflow_path", str(target), source="comfyui.workflow_select")
    return {"ok": True, "path": str(target), **info}


def queue_status() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client

    if settings.comfyui_fake_mode:
        return {"ok": True, "fake_mode": True, "reachable": False, "running": [], "pending": []}
    queue = comfyui_client.get_queue()
    return {
        "ok": True,
        "fake_mode": False,
        "reachable": True,
        "running": queue.get("queue_running") or [],
        "pending": queue.get("queue_pending") or [],
        "raw": queue,
    }


_MONITORING_RUNS: set[str] = set()
_WORKER_RUNS: set[str] = set()


def run_start(
    workflow_path: str | None = None,
    input_dir: str = "",
    output_dir: str = "",
    input_node_id: str | None = None,
    input_name: str = "image",
    max_images: int = 10000,
    recursive: bool = True,
    preserve_structure: bool = True,
    skip_existing: bool = False,
    notify_interval_seconds: int = 300,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.runtime_context import get_runtime_context

    if not input_dir or not output_dir:
        raise ValueError("input_dir and output_dir are required")
    workflow_file = _resolve_workflow_path(workflow_path or _selected_workflow_path() or str(settings.comfyui_workflow_path))
    src = validate_path(input_dir, must_exist=True)
    dst = validate_path(output_dir, must_exist=False)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    if workflow_file.suffix.lower() != ".json":
        raise ValueError("workflow_path must be a json file")
    dst.mkdir(parents=True, exist_ok=True)

    files = _collect_images(src, recursive=recursive, max_images=max_images)
    if not files:
        raise ValueError("input_dir has no supported images")
    if skip_existing:
        files = [path for path in files if not _output_target(src, dst, path, preserve_structure).exists()]

    run_id = _run_id()
    created_at = _now()
    prompt_map: list[dict[str, str]] = []
    ctx = get_runtime_context()
    options = {
        "input_node_id": input_node_id,
        "input_name": input_name,
        "recursive": recursive,
        "preserve_structure": preserve_structure,
        "skip_existing": skip_existing,
        "notify_interval_seconds": max(60, min(notify_interval_seconds, 3600)),
        "prompt_map": prompt_map,
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "archived": False,
    }
    if not settings.comfyui_fake_mode:
        comfyui_client.check_health()

    status = "DONE" if not files else "RUNNING"
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO comfyui_runs
            (id, status, workflow_path, input_dir, output_dir, total, files_json, prompt_ids_json, options_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                status,
                str(workflow_file),
                str(src),
                str(dst),
                len(files),
                json.dumps([str(p) for p in files], ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                json.dumps(options, ensure_ascii=False),
                created_at,
                created_at,
            ),
        )

    if options.get("chat_id") and files:
        _notify(run_id, f"ComfyUI 批量任务已启动：{len(files)} 张\n输入：{src}\n输出：{dst}")
        _start_progress_monitor(run_id)
    if files:
        _start_run_worker(run_id)

    return {
        "ok": True,
        "run_id": run_id,
        "status": status,
        "fake_mode": settings.comfyui_fake_mode,
        "workflow_path": str(workflow_file),
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
        "submitted": 0,
        "prompt_ids": [],
        "recursive": recursive,
        "preserve_structure": preserve_structure,
        "skip_existing": skip_existing,
    }


def run_status(run_id: str | None = None, include_gpu: bool = True) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.skills.status_skills import gpu_status

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}

    prompt_ids = json.loads(row["prompt_ids_json"] or "[]")
    files = json.loads(row["files_json"] or "[]")
    completed = 0
    failed = 0
    histories: list[dict[str, Any]] = []
    options = json.loads(row["options_json"] or "{}")
    prompt_map = options.get("prompt_map") or []
    completed = sum(1 for item in prompt_map if not item.get("error") and Path(str(item.get("dst_path") or "")).exists())
    failed = sum(1 for item in prompt_map if item.get("error"))
    if not settings.comfyui_fake_mode and not completed + failed:
        for prompt_id in prompt_ids:
            try:
                history = comfyui_client.get_history(prompt_id)
                entry = history.get(prompt_id)
                if not entry:
                    continue
                histories.append(entry)
                status = entry.get("status", {})
                if status.get("completed") or status.get("status_str") == "success":
                    completed += 1
                elif status.get("status_str") == "error":
                    failed += 1
            except Exception:
                continue

    total = int(row["total"] or len(files))
    running = max(0, total - completed - failed)
    elapsed = max(0.0, time.time() - datetime.fromisoformat(row["created_at"]).timestamp())
    eta_seconds = _eta(elapsed, completed, total)
    status_text = "DONE" if total and completed + failed >= total else row["status"]
    if failed and completed + failed >= total:
        status_text = "FAILED" if completed == 0 else "DONE_WITH_ERRORS"
    queue = {}
    if not settings.comfyui_fake_mode:
        try:
            queue = comfyui_client.get_queue()
        except Exception as exc:
            queue = {"error": str(exc)}
    with get_connection() as conn:
        conn.execute("UPDATE comfyui_runs SET status = ?, updated_at = ? WHERE id = ?", (status_text, _now(), row["id"]))

    result: dict[str, Any] = {
        "ok": True,
        "run_id": row["id"],
        "status": status_text,
        "fake_mode": settings.comfyui_fake_mode,
        "workflow_path": row["workflow_path"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "total": total,
        "completed": completed,
        "failed": failed,
        "running_or_pending": running,
        "progress_percent": round((completed + failed) / total * 100, 1) if total else 0,
        "eta_seconds": eta_seconds,
        "queue_running": len(queue.get("queue_running") or []),
        "queue_pending": len(queue.get("queue_pending") or []),
        "prompt_ids": prompt_ids[:20],
        "last_completed": _last_completed_name(prompt_map),
        "last_completed_detail": _path_detail(_last_completed_rel_path(prompt_map)),
    }
    if include_gpu:
        result["gpu"] = gpu_status()
    return result


def run_sync_outputs(run_id: str, overwrite: bool = True) -> dict[str, Any]:
    from assetclaw_matting.comfyui.client import comfyui_client

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    output_dir = validate_path(row["output_dir"], must_exist=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_ids = json.loads(row["prompt_ids_json"] or "[]")
    options = json.loads(row["options_json"] or "{}")
    prompt_map = options.get("prompt_map") or []
    prompt_targets = {item.get("prompt_id"): item for item in prompt_map if item.get("prompt_id")}
    saved = []
    for prompt_id in prompt_ids:
        history = comfyui_client.get_history(prompt_id)
        if prompt_id not in history:
            continue
        output = resolve_first_output(history, prompt_id)
        mapped = prompt_targets.get(prompt_id) or {}
        target = Path(mapped.get("dst_path") or (output_dir / output["filename"]))
        if target.exists() and not overwrite:
            continue
        comfyui_client.download_output(output["filename"], output.get("subfolder", ""), output.get("type", "output"), target)
        saved.append(str(target))
    return {"ok": True, "run_id": run_id, "output_dir": str(output_dir), "count": len(saved), "items": saved[:50]}


def run_pause(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    if row["status"] in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
        return {"ok": True, "run_id": row["id"], "status": row["status"], "message": "任务已经结束，不能暂停。"}
    _set_run_status(row["id"], "PAUSED")
    _notify(row["id"], f"ComfyUI 任务已暂停：{row['id']}")
    return {"ok": True, "run_id": row["id"], "status": "PAUSED"}


def run_resume(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    if row["status"] != "PAUSED":
        return {"ok": True, "run_id": row["id"], "status": row["status"], "message": "当前任务不在暂停状态。"}
    _set_run_status(row["id"], "RUNNING")
    _notify(row["id"], f"ComfyUI 任务已继续：{row['id']}")
    _start_run_worker(row["id"])
    _start_progress_monitor(row["id"])
    return {"ok": True, "run_id": row["id"], "status": "RUNNING"}


def run_cancel(run_id: str | None = None, interrupt_current: bool = True) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    _set_run_status(row["id"], "CANCELED")
    prompt_ids = json.loads(row["prompt_ids_json"] or "[]")
    queue_error = ""
    if not settings.comfyui_fake_mode:
        try:
            if prompt_ids:
                comfyui_client.delete_from_queue(prompt_ids)
            if interrupt_current:
                comfyui_client.interrupt()
        except Exception as exc:
            queue_error = str(exc)
    _notify(row["id"], f"ComfyUI 任务已终止：{row['id']}")
    return {"ok": True, "run_id": row["id"], "status": "CANCELED", "queue_error": queue_error}


def run_preview(
    workflow_path: str | None = None,
    input_dir: str = "",
    output_dir: str = "",
    recursive: bool = True,
    preserve_structure: bool = True,
    max_images: int = 10000,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    workflow_file = _resolve_workflow_path(workflow_path or _selected_workflow_path() or str(settings.comfyui_workflow_path))
    src = validate_path(input_dir or "E:\\input", must_exist=True)
    dst = validate_path(output_dir or "E:\\output", must_exist=False)
    workflow = json.loads(workflow_file.read_text(encoding="utf-8"))
    info = inspect_workflow(workflow)
    files = _collect_images(src, recursive=recursive, max_images=max_images)
    return {
        "ok": True,
        "workflow_path": str(workflow_file),
        "workflow_name": workflow_file.name,
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
        "sample_inputs": [str(path.relative_to(src)) for path in files[:8]],
        "recursive": recursive,
        "preserve_structure": preserve_structure,
        **info,
    }


def run_list(limit: int = 10, include_archived: bool = False, include_finished: bool = False) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, status, workflow_path, input_dir, output_dir, total, options_json, created_at, updated_at
            FROM comfyui_runs
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
        prompt_map = options.get("prompt_map") or []
        completed = sum(1 for item in prompt_map if not item.get("error") and Path(str(item.get("dst_path") or "")).exists())
        failed = sum(1 for item in prompt_map if item.get("error"))
        items.append({
            "run_id": row["id"],
            "status": row["status"],
            "workflow_name": Path(row["workflow_path"]).name,
            "workflow_path": row["workflow_path"],
            "input_dir": row["input_dir"],
            "output_dir": row["output_dir"],
            "total": int(row["total"] or 0),
            "completed": completed,
            "failed": failed,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return {"ok": True, "count": len(items), "items": items}


def run_delete(run_id: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    if row["status"] not in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
        return {
            "ok": False,
            "error": "任务还在运行中。先终止或等它结束，再删除记录。",
            "run_id": row["id"],
            "status": row["status"],
        }
    options = json.loads(row["options_json"] or "{}")
    options["archived"] = True
    with get_connection() as conn:
        conn.execute(
            "UPDATE comfyui_runs SET options_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(options, ensure_ascii=False), _now(), row["id"]),
        )
    return {"ok": True, "run_id": row["id"], "status": "ARCHIVED"}


def run_update(
    run_id: str | None = None,
    workflow_path: str | None = None,
    input_dir: str | None = None,
    output_dir: str | None = None,
    recursive: bool | None = None,
    preserve_structure: bool | None = None,
    max_images: int | None = None,
) -> dict[str, Any]:
    from assetclaw_matting.db.sqlite import get_connection

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    options = json.loads(row["options_json"] or "{}")
    prompt_map = options.get("prompt_map") or []
    if prompt_map and row["status"] not in {"PAUSED", "QUEUED"}:
        raise ValueError("任务已经开始产出，先暂停或终止后再改参数。")

    workflow = _resolve_workflow_path(workflow_path or row["workflow_path"])
    src = validate_path(input_dir or row["input_dir"], must_exist=True)
    dst = validate_path(output_dir or row["output_dir"], must_exist=False)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    if recursive is not None:
        options["recursive"] = bool(recursive)
    if preserve_structure is not None:
        options["preserve_structure"] = bool(preserve_structure)
    files = _collect_images(src, recursive=bool(options.get("recursive", True)), max_images=max_images or 10000)
    if not files:
        raise ValueError("input_dir has no supported images")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE comfyui_runs
            SET workflow_path = ?, input_dir = ?, output_dir = ?, total = ?, files_json = ?, options_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(workflow),
                str(src),
                str(dst),
                len(files),
                json.dumps([str(path) for path in files], ensure_ascii=False),
                json.dumps(options, ensure_ascii=False),
                _now(),
                row["id"],
            ),
        )
    return {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "workflow_path": str(workflow),
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
    }


def preview_run_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    try:
        preview = run_preview(
            workflow_path=arguments.get("workflow_path"),
            input_dir=arguments.get("input_dir") or "E:\\input",
            output_dir=arguments.get("output_dir") or "E:\\output",
            recursive=bool(arguments.get("recursive", True)),
            preserve_structure=bool(arguments.get("preserve_structure", True)),
            max_images=int(arguments.get("max_images") or 10000),
        )
        lines = [
            "请确认是否开始 ComfyUI 批量抠图：",
            f"工作流：{preview.get('workflow_name')}",
            f"输入：{preview.get('input_dir')}",
            f"输出：{preview.get('output_dir')}",
            f"图片：{preview.get('total')} 张",
            f"节点：{preview.get('node_count')} 个，LoadImage {len(preview.get('load_image_nodes') or [])} 个，SaveImage {len(preview.get('save_image_nodes') or [])} 个",
        ]
        samples = preview.get("sample_inputs") or []
        if samples:
            lines.append("示例：" + "、".join(samples[:3]))
        lines.append(f"回复：确认执行 {confirmation_id}")
        return "\n".join(lines)
    except Exception as exc:
        return f"需要确认：comfyui.run_start\n预检查失败：{exc}\n回复：确认执行 {confirmation_id}"


def _start_run_worker(run_id: str) -> None:
    if run_id in _WORKER_RUNS:
        return
    _WORKER_RUNS.add(run_id)
    thread = threading.Thread(target=_run_worker, args=(run_id,), daemon=True)
    thread.start()


def _run_worker(run_id: str) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client

    try:
        row = _get_run(run_id)
        if not row:
            return
        files = [Path(path) for path in json.loads(row["files_json"] or "[]")]
        options = json.loads(row["options_json"] or "{}")
        prompt_map = options.get("prompt_map") or []
        prompt_ids = json.loads(row["prompt_ids_json"] or "[]")
        src = Path(row["input_dir"])
        dst = Path(row["output_dir"])
        workflow_file = Path(row["workflow_path"])
        base_workflow = json.loads(workflow_file.read_text(encoding="utf-8"))
        done_sources = {item.get("src_path") for item in prompt_map}

        for image_path in files:
            if str(image_path) in done_sources:
                continue
            while True:
                latest = _get_run(run_id)
                if not latest or latest["status"] == "CANCELED":
                    return
                if latest["status"] != "PAUSED":
                    break
                time.sleep(5)
            _set_run_status(run_id, "RUNNING")
            target = _output_target(src, dst, image_path, bool(options.get("preserve_structure", True)))
            prompt_id = ""
            try:
                if settings.comfyui_fake_mode:
                    _fake_copy_image(image_path, target)
                else:
                    uploaded = comfyui_client.upload_image(image_path)
                    workflow = copy.deepcopy(base_workflow)
                    if options.get("input_node_id"):
                        workflow = patch_node_input(workflow, str(options["input_node_id"]), str(options.get("input_name") or "image"), uploaded)
                    else:
                        workflow = patch_load_image(workflow, uploaded)
                    prompt_id = comfyui_client.submit_prompt(prepare_api_prompt_for_run(workflow), client_id=run_id)
                    prompt_ids.append(prompt_id)
                    _save_run_progress(run_id, prompt_ids, options)
                    history = comfyui_client.wait_for_completion(prompt_id)
                    output = resolve_first_output(history, prompt_id)
                    comfyui_client.download_output(output["filename"], output.get("subfolder", ""), output.get("type", "output"), target)
                prompt_map.append({
                    "prompt_id": prompt_id,
                    "src_path": str(image_path),
                    "rel_path": _relative_output_key(src, image_path, bool(options.get("preserve_structure", True))),
                    "dst_path": str(target),
                })
                options["prompt_map"] = prompt_map
                _save_run_progress(run_id, prompt_ids, options)
            except Exception as exc:
                prompt_map.append({
                    "prompt_id": prompt_id,
                    "src_path": str(image_path),
                    "rel_path": _relative_output_key(src, image_path, bool(options.get("preserve_structure", True))),
                    "dst_path": str(target),
                    "error": str(exc),
                })
                options["prompt_map"] = prompt_map
                _save_run_progress(run_id, prompt_ids, options)
                _set_run_status(run_id, "FAILED")
                _notify(run_id, f"ComfyUI 抠图出错：{image_path.name}\n{exc}")
                return
        final_status = "DONE_WITH_ERRORS" if any(item.get("error") for item in prompt_map) else "DONE"
        _set_run_status(run_id, final_status)
    except Exception as exc:
        _set_run_status(run_id, "FAILED")
        _notify(run_id, f"ComfyUI 批量任务异常：{exc}")
    finally:
        _WORKER_RUNS.discard(run_id)


def _start_progress_monitor(run_id: str) -> None:
    if run_id in _MONITORING_RUNS:
        return
    _MONITORING_RUNS.add(run_id)
    thread = threading.Thread(target=_monitor_run, args=(run_id,), daemon=True)
    thread.start()


def _monitor_run(run_id: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    last_completed = -1
    last_status = ""
    try:
        while True:
            row = _get_run(run_id)
            if not row:
                return
            options = json.loads(row["options_json"] or "{}")
            chat_id = options.get("chat_id")
            if not chat_id:
                return
            interval = int(options.get("notify_interval_seconds") or 60)
            status = run_status(run_id, include_gpu=True)
            text = _format_progress_notification(status)
            completed = int(status.get("completed") or 0)
            status_text = str(status.get("status") or "")
            should_notify = _should_notify_progress(
                status=status,
                last_completed=last_completed,
                last_status=last_status,
            )
            if should_notify:
                _notify(run_id, text)
                last_completed = completed
                last_status = status_text
            if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED"}:
                synced = 0
                if not status.get("fake_mode") and status.get("completed", 0):
                    try:
                        synced_result = run_sync_outputs(run_id)
                        synced = int(synced_result.get("count") or 0)
                    except Exception as exc:
                        with get_connection() as conn:
                            conn.execute("UPDATE comfyui_runs SET status = ?, updated_at = ? WHERE id = ?", ("FAILED", _now(), run_id))
                        _notify(run_id, f"ComfyUI 输出同步失败：{exc}")
                        return
                suffix = f"\n已同步输出：{synced} 个" if synced else ""
                _notify(run_id, f"ComfyUI 批量任务完成：{status.get('completed', 0)}/{status.get('total', 0)} 张{suffix}")
                return
            time.sleep(max(10, interval))
    except Exception as exc:
        with get_connection() as conn:
            conn.execute("UPDATE comfyui_runs SET status = ?, updated_at = ? WHERE id = ?", ("FAILED", _now(), run_id))
        _notify(run_id, f"ComfyUI 批量任务异常：{exc}")
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
        f"ComfyUI 进度：{status.get('completed', 0)}/{status.get('total', 0)} ({status.get('progress_percent', 0)}%)",
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


def _should_notify_progress(status: dict[str, Any], last_completed: int, last_status: str) -> bool:
    status_text = str(status.get("status") or "")
    completed = int(status.get("completed") or 0)
    total = int(status.get("total") or 0)
    if status_text != last_status:
        return True
    if status_text in {"DONE", "DONE_WITH_ERRORS", "FAILED"}:
        return True
    if last_completed < 0:
        return False
    if total <= 0:
        return False
    step = max(1, total // 20)
    return completed - last_completed >= step


def _default_workflow_roots() -> list[Path]:
    from assetclaw_matting.config import settings

    candidates = [settings.comfyui_dir / "user" / "default" / "workflows"]
    roots = []
    for root in candidates:
        try:
            validated = validate_path(root, must_exist=True)
        except Exception:
            continue
        if validated.is_dir():
            roots.append(validated)
    return roots


def _iter_json_files(roots: list[Path], limit: int) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix.lower() == ".json":
            files.append(root)
            continue
        if not root.is_dir():
            continue
        for item in sorted(root.rglob("*.json"), key=lambda p: str(p).lower()):
            files.append(item)
            if len(files) >= limit:
                return files
    return files


def _safe_workflow_brief(path: Path) -> dict[str, Any]:
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
        info = inspect_workflow(workflow)
        return {
            "node_count": info.get("node_count", 0),
            "load_image_count": len(info.get("load_image_nodes") or []),
            "save_image_count": len(info.get("save_image_nodes") or []),
        }
    except Exception:
        return {"node_count": None, "load_image_count": None, "save_image_count": None}


def _resolve_workflow_path(raw: str | Path) -> Path:
    text = str(raw or "").strip().strip('"')
    if not text:
        raise ValueError("workflow path is required")
    candidate = Path(text)
    if candidate.exists():
        target = validate_path(candidate, must_exist=True)
    else:
        matches = []
        name = text if text.lower().endswith(".json") else f"{text}.json"
        for root in _default_workflow_roots():
            exact = root / name
            if exact.exists():
                matches.append(exact)
            matches.extend(path for path in root.rglob("*.json") if text.lower() in path.name.lower())
        unique: list[Path] = []
        seen = set()
        for item in matches:
            key = str(item).lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        if not unique:
            raise FileNotFoundError(f"workflow not found: {text}")
        if len(unique) > 1:
            names = "；".join(str(item) for item in unique[:5])
            raise ValueError(f"matched multiple workflows, please choose one: {names}")
        target = validate_path(unique[0], must_exist=True)
    if target.suffix.lower() != ".json":
        raise ValueError("workflow path must be a json file")
    return target


def _selected_workflow_path() -> str | None:
    from assetclaw_matting.db.repos import list_memory_notes
    from assetclaw_matting.runtime_context import get_runtime_context

    scopes = []
    ctx = get_runtime_context()
    conversation_id = ctx.get("conversation_id")
    if conversation_id:
        scopes.append(conversation_id)
    scopes.append("global")
    for scope in scopes:
        for item in list_memory_notes(scope, limit=20):
            if item.get("key") == "selected_comfyui_workflow_path":
                path = str(item.get("value") or "")
                if path and Path(path).exists():
                    return path
    return None


def _collect_images(root: Path, recursive: bool, max_images: int) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [p for p in iterator if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    files = sorted(files, key=lambda p: str(p.relative_to(root)).lower())
    return files[: max(1, min(max_images, 10000))]


def _relative_output_key(input_root: Path, image_path: Path, preserve_structure: bool) -> str:
    rel = image_path.relative_to(input_root) if preserve_structure else Path(image_path.name)
    return str(rel.with_suffix(".png"))


def _output_target(input_root: Path, output_root: Path, image_path: Path, preserve_structure: bool) -> Path:
    return output_root / _relative_output_key(input_root, image_path, preserve_structure)


def _fake_copy_image(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        with Image.open(src) as image:
            image.convert("RGBA").save(dst, "PNG")
    except Exception:
        shutil.copy2(src, dst)


def _last_completed_name(prompt_map: list[dict[str, str]]) -> str:
    completed = [Path(str(item.get("src_path") or item.get("dst_path") or "")).name for item in prompt_map if Path(str(item.get("dst_path") or "")).exists()]
    return completed[-1] if completed else ""


def _last_completed_rel_path(prompt_map: list[dict[str, str]]) -> str:
    completed = [str(item.get("rel_path") or "") for item in prompt_map if Path(str(item.get("dst_path") or "")).exists()]
    return completed[-1] if completed else ""


def _path_detail(rel_path: str) -> dict[str, str]:
    if not rel_path:
        return {}
    parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
    if len(parts) >= 4 and parts[-2].lower().startswith("video_"):
        role = parts[-4]
        emotion = parts[-3]
    else:
        role = parts[-3] if len(parts) >= 3 else (parts[-2] if len(parts) >= 2 else "")
        emotion = parts[-2] if len(parts) >= 2 else ""
    frame = parts[-1] if parts else ""
    return {"role": role, "emotion": emotion, "frame": frame, "rel_path": rel_path}


def _get_run(run_id: str | None) -> Any:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        if run_id:
            return conn.execute("SELECT * FROM comfyui_runs WHERE id = ?", (run_id,)).fetchone()
        active = conn.execute(
            """
            SELECT * FROM comfyui_runs
            WHERE status NOT IN ('DONE', 'DONE_WITH_ERRORS', 'FAILED', 'CANCELED')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if active:
            return active
        return conn.execute("SELECT * FROM comfyui_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def _set_run_status(run_id: str, status: str) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE comfyui_runs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), run_id))


def _save_run_progress(run_id: str, prompt_ids: list[str], options: dict[str, Any]) -> None:
    from assetclaw_matting.db.sqlite import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE comfyui_runs SET prompt_ids_json = ?, options_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(prompt_ids, ensure_ascii=False), json.dumps(options, ensure_ascii=False), _now(), run_id),
        )


def _eta(elapsed: float, completed: int, total: int) -> int | None:
    if completed <= 0 or total <= completed:
        return None
    avg = elapsed / completed
    return int(avg * (total - completed))


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"

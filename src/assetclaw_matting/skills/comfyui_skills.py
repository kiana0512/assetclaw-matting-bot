from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from assetclaw_matting.comfyui.output_resolver import inspect_local_png, resolve_best_output
from assetclaw_matting.comfyui.workflow_patch import find_primary_save_image_node_id, inspect_workflow, patch_load_image, patch_node_input, prepare_api_prompt_for_run
from assetclaw_matting.ops_trace import trace as ops_trace
from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.security import validate_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_gpu_stage(remote_state: dict[str, Any], name: str, *, overwrite: bool = False) -> str:
    stages = dict(remote_state.get("client_stages") or {})
    timestamp = _now()
    if overwrite or not stages.get(name):
        stages[name] = timestamp
    remote_state["client_stages"] = stages
    return str(stages[name])


def _trace_gpu_stage(run_id: str, remote_state: dict[str, Any], stage: str, **fields: Any) -> None:
    try:
        ops_trace(
            "gpu_control_stage",
            run_id=run_id,
            trace_id=remote_state.get("trace_id") or "",
            external_batch_id=remote_state.get("external_batch_id") or "",
            remote_batch_id=remote_state.get("batch_id") or "",
            stage=stage,
            **fields,
        )
    except Exception:
        # Observability must never stop submission, reconciliation, or an
        # explicit operator cancellation.
        return


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
    running = [_queue_item_summary(item) for item in (queue.get("queue_running") or [])]
    pending = [_queue_item_summary(item) for item in (queue.get("queue_pending") or [])]
    return {
        "ok": True,
        "fake_mode": False,
        "reachable": True,
        "running": running,
        "pending": pending,
        "running_count": len(running),
        "pending_count": len(pending),
    }


def _queue_item_summary(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return {
            "position": item.get("number") or item.get("position"),
            "prompt_id": item.get("prompt_id") or item.get("id") or "",
            "client_id": item.get("client_id") or "",
        }
    if isinstance(item, (list, tuple)):
        metadata = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
        return {
            "position": item[0] if item else None,
            "prompt_id": str(item[1]) if len(item) > 1 else "",
            "client_id": str(metadata.get("client_id") or ""),
        }
    return {"position": None, "prompt_id": "", "client_id": ""}


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
    priority_characters: list[str] | None = None,
    notify_interval_seconds: int = 300,
    strict_frame_identity: bool = False,
    backend: str | None = None,
    external_batch_id: str | None = None,
    cluster_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client
    from assetclaw_matting.db.sqlite import get_connection
    from assetclaw_matting.runtime_context import get_runtime_context

    if not input_dir or not output_dir:
        raise ValueError("input_dir and output_dir are required")
    selected_workflow = _selected_workflow_path()
    pipeline_notice = ""
    # GPU Control owns ImageClip workflow rollout and pins the workflow
    # identity per batch. Never pull/sync the local ImageClip repository from
    # this remote matting entry point; Cherry refresh is performed by the
    # parent animation/image task before processing starts.
    workflow_file = _resolve_workflow_path(workflow_path or selected_workflow or str(settings.comfyui_workflow_path))
    src = validate_path(input_dir, must_exist=True)
    dst = validate_path(output_dir, must_exist=False)
    if not src.is_dir():
        raise ValueError("input_dir must be a directory")
    if workflow_file.suffix.lower() != ".json":
        raise ValueError("workflow_path must be a json file")
    dst.mkdir(parents=True, exist_ok=True)

    files = _collect_images(src, recursive=recursive, max_images=max_images, priority_characters=priority_characters)
    if not files:
        raise ValueError("input_dir has no supported images")
    if skip_existing:
        files = [path for path in files if not _output_target(src, dst, path, preserve_structure).exists()]

    run_id = _run_id()
    created_at = _now()
    prompt_map: list[dict[str, str]] = []
    ctx = get_runtime_context()
    from assetclaw_matting.services.hybrid_matting_router import matting_route_lock, select_matting_backend

    options = {
        "input_node_id": input_node_id,
        "input_name": input_name,
        "recursive": recursive,
        "preserve_structure": preserve_structure,
        "skip_existing": skip_existing,
        "priority_characters": list(priority_characters or []),
        "notify_interval_seconds": max(60, min(notify_interval_seconds, 3600)),
        "strict_frame_identity": bool(strict_frame_identity),
        "prompt_map": prompt_map,
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "archived": False,
        "pipeline_notice": pipeline_notice,
        "external_batch_id": external_batch_id or f"assetclaw:{run_id}:matting:g1",
        "trace_id": f"assetclaw-{run_id.lower()}",
        "cluster_parameters": dict(cluster_parameters or {}),
    }

    status = "DONE" if not files else "RUNNING"
    with matting_route_lock():
        selected_backend, selection_reason, backend_handshake = select_matting_backend(
            len(files),
            requested=backend,
            include_handshake=True,
        )
        options["matting_backend"] = selected_backend
        options["backend_selection_reason"] = selection_reason
        options["backend_handshake"] = backend_handshake
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

    if selected_backend == "local" and not settings.comfyui_fake_mode:
        try:
            comfyui_client.check_health()
        except Exception:
            _set_run_status(run_id, "FAILED")
            raise

    if options.get("chat_id") and files:
        notice = f"\n{pipeline_notice}" if pipeline_notice else ""
        backend_label = "GPU Control 集群"
        _notify(run_id, f"抠图批量任务已启动：{len(files)} 张，后端：{backend_label}{notice}\n输入：{src}\n输出：{dst}")
        _start_progress_monitor(run_id)
    if files:
        _start_run_worker(run_id)

    return {
        "ok": True,
        "run_id": run_id,
        "status": status,
        "fake_mode": settings.comfyui_fake_mode,
        "workflow_path": str(workflow_file),
        "pipeline_notice": pipeline_notice,
        "input_dir": str(src),
        "output_dir": str(dst),
        "total": len(files),
        "submitted": 0,
        "prompt_ids": [],
        "recursive": recursive,
        "preserve_structure": preserve_structure,
        "skip_existing": skip_existing,
        "backend": selected_backend,
        "backend_selection_reason": selection_reason,
        "external_batch_id": options["external_batch_id"],
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
    options = json.loads(row["options_json"] or "{}")
    backend = str(options.get("matting_backend") or "local")
    remote_state = dict(options.get("gpu_control") or {})
    prompt_map = options.get("prompt_map") or []
    if backend == "gpu_control" and row["status"] not in {"DONE", "DONE_WITH_ERRORS"}:
        counts = dict(remote_state.get("counts") or {})
        completed = int(counts.get("succeeded") or 0)
        failed = int(counts.get("failed") or 0)
    else:
        completed = sum(1 for item in prompt_map if not item.get("error") and Path(str(item.get("dst_path") or "")).exists())
        failed = sum(1 for item in prompt_map if item.get("error"))
    error_items = [item for item in prompt_map if item.get("error")]
    last_error_detail = (
        dict(error_items[-1].get("error_detail") or {})
        if error_items and isinstance(error_items[-1].get("error_detail"), dict)
        else {}
    )
    remote_error = remote_state.get("client_error") or remote_state.get("error") or remote_state.get("poll_error") or ""
    total = int(row["total"] or len(files))
    running = max(0, total - completed - failed)
    eta_seconds = _eta_from_prompt_map(prompt_map, total, row["created_at"])
    status_text = row["status"]
    # Prompt success only means ComfyUI executed the graph. Completion requires
    # a validated transparent PNG to exist at dst_path, otherwise a parent flow
    # can enter post-processing while this worker is still rejecting/retrying.
    if backend == "local" and total and completed + failed >= total:
        if failed:
            status_text = "FAILED" if completed == 0 else "DONE_WITH_ERRORS"
        elif completed == total:
            status_text = "DONE"
    queue = {}
    if backend == "local" and not settings.comfyui_fake_mode:
        try:
            queue = comfyui_client.get_queue()
        except Exception as exc:
            queue = {"error": str(exc)}
    if status_text != row["status"]:
        with get_connection() as conn:
            conn.execute("UPDATE comfyui_runs SET status = ?, updated_at = ? WHERE id = ?", (status_text, _now(), row["id"]))

    result: dict[str, Any] = {
        "ok": True,
        "run_id": row["id"],
        "status": status_text,
        "backend": backend,
        "backend_selection_reason": options.get("backend_selection_reason") or "",
        "backend_handshake": options.get("backend_handshake") or {},
        "external_batch_id": options.get("external_batch_id") or "",
        "trace_id": remote_state.get("trace_id") or options.get("trace_id") or "",
        "remote_batch_id": remote_state.get("batch_id") or "",
        "remote_status": remote_state.get("status") or "",
        "node_distribution": remote_state.get("node_distribution") or {},
        "workflow_identity": remote_state.get("identity_validation") or {},
        "remote_performance": remote_state.get("performance") or {},
        "client_stages": remote_state.get("client_stages") or {},
        "idle_gap_seconds": remote_state.get("idle_gap_seconds"),
        "poll_errors_total": int(remote_state.get("poll_errors_total") or 0),
        "watchdog_action": remote_state.get("watchdog_action") or "",
        "cancel_intent": remote_state.get("cancel_intent") or {},
        "fake_mode": settings.comfyui_fake_mode,
        "workflow_path": row["workflow_path"],
        "input_dir": row["input_dir"],
        "output_dir": row["output_dir"],
        "total": total,
        "completed": completed,
        "failed": failed,
        "running_or_pending": running,
        "progress_percent": (
            round(float(remote_state.get("progress") or 0), 1)
            if backend == "gpu_control" and remote_state.get("progress") is not None
            else (round((completed + failed) / total * 100, 1) if total else 0)
        ),
        "eta_seconds": eta_seconds,
        "queue_running": len(queue.get("queue_running") or []),
        "queue_pending": len(queue.get("queue_pending") or []),
        "prompt_ids": prompt_ids[:20],
        "last_completed": _last_completed_name(prompt_map),
        "last_completed_detail": _path_detail(_last_completed_rel_path(prompt_map)),
        "last_error": str(remote_error) if backend == "gpu_control" and remote_error else _last_error_summary(error_items),
        "last_error_kind": str(last_error_detail.get("kind") or ""),
        "last_error_detail": last_error_detail,
        "error_items": _error_item_summaries(error_items[:5]),
        "updated_at": row["updated_at"],
    }
    if include_gpu:
        result["gpu"] = (
            {
                "source": "gpu_control",
                "batch_id": remote_state.get("batch_id") or "",
                "node_distribution": remote_state.get("node_distribution") or {},
            }
            if backend == "gpu_control"
            else gpu_status()
        )
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
    if str(options.get("matting_backend") or "local") == "gpu_control":
        saved = [str(item.get("dst_path")) for item in prompt_map if Path(str(item.get("dst_path") or "")).is_file()]
        return {"ok": True, "run_id": run_id, "output_dir": str(output_dir), "count": len(saved), "items": saved[:50]}
    prompt_targets = {item.get("prompt_id"): item for item in prompt_map if item.get("prompt_id")}
    workflow_file = Path(row["workflow_path"])
    final_save_image_node_id = _final_save_image_node_id(json.loads(workflow_file.read_text(encoding="utf-8")))
    saved = []
    for prompt_id in prompt_ids:
        history = comfyui_client.get_history(prompt_id)
        if prompt_id not in history:
            continue
        output = resolve_best_output(
            history,
            prompt_id,
            local_path_resolver=comfyui_client.resolve_local_output_path,
            final_save_image_node_id=final_save_image_node_id,
        )
        mapped = prompt_targets.get(prompt_id) or {}
        target = Path(mapped.get("dst_path") or (output_dir / output["filename"]))
        if target.exists() and not overwrite:
            continue
        comfyui_client.download_output(output["filename"], output.get("subfolder", ""), output.get("type", "output"), target)
        _ensure_final_transparent_png(target)
        saved.append(str(target))
    return {"ok": True, "run_id": run_id, "output_dir": str(output_dir), "count": len(saved), "items": saved[:50]}


def run_pause(run_id: str | None = None) -> dict[str, Any]:
    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    if row["status"] in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
        return {"ok": True, "run_id": row["id"], "status": row["status"], "message": "任务已经结束，不能暂停。"}
    options = json.loads(row["options_json"] or "{}")
    if str(options.get("matting_backend") or "local") == "gpu_control":
        return {
            "ok": False,
            "run_id": row["id"],
            "status": row["status"],
            "error": "GPU Control batch does not support pause; cancel it instead",
        }
    _set_run_status(row["id"], "PAUSED")
    _notify(row["id"], f"ComfyUI 任务已暂停：{row['id']}")
    return {"ok": True, "run_id": row["id"], "status": "PAUSED"}


def run_resume(run_id: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    if row["status"] in {"DONE", "DONE_WITH_ERRORS", "CANCELED"}:
        return {"ok": True, "run_id": row["id"], "status": row["status"], "message": "任务已经结束，不能继续。"}
    options = json.loads(row["options_json"] or "{}")
    backend = str(options.get("matting_backend") or "local")
    if row["status"] == "RUNNING" and backend == "local" and not settings.comfyui_fake_mode:
        try:
            queue = comfyui_client.get_queue()
            queue_count = len(queue.get("queue_running") or []) + len(queue.get("queue_pending") or [])
        except Exception:
            queue_count = 0
        if queue_count:
            return {"ok": True, "run_id": row["id"], "status": "RUNNING", "message": "任务已经在 ComfyUI 队列中运行。"}
        if not _looks_stalled(row):
            return {"ok": True, "run_id": row["id"], "status": "RUNNING", "message": "任务刚更新过，暂不重复拉起 worker。"}
    _set_run_status(row["id"], "RUNNING")
    _notify(row["id"], f"ComfyUI 任务已继续/重新拉起：{row['id']}")
    _start_run_worker(row["id"])
    _start_progress_monitor(row["id"])
    return {"ok": True, "run_id": row["id"], "status": "RUNNING", "message": "已拉起提交 worker。"}


def run_cancel(run_id: str | None = None, interrupt_current: bool = True, notify: bool = True) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.comfyui.client import comfyui_client

    row = _get_run(run_id)
    if not row:
        return {"ok": False, "error": "comfyui run not found"}
    prompt_ids = json.loads(row["prompt_ids_json"] or "[]")
    options = json.loads(row["options_json"] or "{}")
    backend = str(options.get("matting_backend") or "local")
    queue_error = ""
    status = "CANCELED"
    if backend == "gpu_control":
        remote_state = dict(options.get("gpu_control") or {})
        remote_batch_id = str(remote_state.get("batch_id") or "")
        cancel_key = f"{options.get('external_batch_id') or row['id']}:cancel"
        cancel_request_id = f"{row['id'].lower()}-cancel-01"
        cancel_intent = dict(remote_state.get("cancel_intent") or {})
        if not cancel_intent:
            cancel_intent = {
                "actor": "assetclaw_operator",
                "source": "assetclaw.comfyui.run_cancel",
                "reason": "explicit_user_request",
                "request_id": cancel_request_id,
                "idempotency_key": cancel_key,
                "requested_at": _now(),
            }
        remote_state["cancel_intent"] = cancel_intent
        options["gpu_control"] = remote_state
        # Persist intent before the network call so a crash cannot produce an
        # unaudited remote CANCELLED state.
        _save_run_progress(row["id"], prompt_ids, options)
        _trace_gpu_stage(row["id"], remote_state, "cancel_intent_persisted", request_id=cancel_request_id)
        if remote_batch_id:
            try:
                from assetclaw_matting.services.gpu_control_batch import GpuControlBatchClient, merge_remote_state

                response = GpuControlBatchClient().cancel_batch(
                    remote_batch_id,
                    idempotency_key=cancel_key,
                    request_id=cancel_request_id,
                )
                latest = _get_run(row["id"])
                latest_options = json.loads(latest["options_json"] or "{}") if latest else options
                latest_remote = dict(latest_options.get("gpu_control") or remote_state)
                latest_remote = merge_remote_state(latest_remote, response)
                latest_remote["cancel_requested_at"] = _now()
                latest_remote["cancel_response_meta"] = dict(response.get("_response_meta") or {})
                latest_intent = dict(latest_remote.get("cancel_intent") or cancel_intent)
                latest_intent["accepted_at"] = _now()
                latest_intent["remote_status"] = str(response.get("status") or "")
                latest_remote["cancel_intent"] = latest_intent
                latest_options["gpu_control"] = latest_remote
                _save_run_progress(row["id"], prompt_ids, latest_options)
                status = "CANCELING"
            except Exception as exc:
                queue_error = str(exc)
                status = str(row["status"] or "RUNNING")
        # If create has not returned a batch id, the create worker observes
        # CANCELED and submits the exact <external_batch_id>:cancel key as soon
        # as the remote id becomes known.
        _set_run_status(row["id"], status)
    elif not settings.comfyui_fake_mode:
        _set_run_status(row["id"], status)
        try:
            if prompt_ids:
                comfyui_client.delete_from_queue(prompt_ids)
            if interrupt_current:
                comfyui_client.interrupt()
        except Exception as exc:
            queue_error = str(exc)
    else:
        _set_run_status(row["id"], status)
    if notify:
        message = (
            f"GPU Control 取消请求已提交，正在等待远端收敛：{row['id']}"
            if status == "CANCELING"
            else f"ComfyUI 任务已终止：{row['id']}"
        )
        _notify(row["id"], message)
    return {
        "ok": not bool(queue_error),
        "run_id": row["id"],
        "status": status,
        "backend": backend,
        "queue_error": queue_error,
    }


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
    src = validate_path(input_dir or settings.default_batch_input_dir, must_exist=True)
    dst = validate_path(output_dir or settings.default_batch_output_dir, must_exist=False)
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
        backend = str(options.get("matting_backend") or "local")
        remote_state = dict(options.get("gpu_control") or {})
        if backend == "gpu_control" and row["status"] not in {"DONE", "DONE_WITH_ERRORS"}:
            counts = dict(remote_state.get("counts") or {})
            completed = int(counts.get("succeeded") or 0)
            failed = int(counts.get("failed") or 0)
        else:
            completed = sum(1 for item in prompt_map if not item.get("error") and Path(str(item.get("dst_path") or "")).exists())
            failed = sum(1 for item in prompt_map if item.get("error"))
        items.append({
            "run_id": row["id"],
            "status": row["status"],
            "backend": backend,
            "remote_batch_id": remote_state.get("batch_id") or "",
            "remote_status": remote_state.get("status") or "",
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
    if str(options.get("matting_backend") or "local") == "gpu_control" and (options.get("gpu_control") or {}).get("batch_id"):
        raise ValueError("GPU Control batch has already been submitted; cancel it and create a new run instead")
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
            input_dir=arguments.get("input_dir") or str(settings.default_batch_input_dir),
            output_dir=arguments.get("output_dir") or str(settings.default_batch_output_dir),
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
    row = _get_run(run_id)
    if not row:
        return
    options = json.loads(row["options_json"] or "{}")
    worker = _run_gpu_control_worker if str(options.get("matting_backend") or "local") == "gpu_control" else _run_worker
    _WORKER_RUNS.add(run_id)
    thread = threading.Thread(target=worker, args=(run_id,), daemon=True)
    thread.start()


def recover_gpu_control_manifest_failures(limit: int = 50) -> dict[str, Any]:
    """Re-verify current parents whose completed GPU artifact hit the old manifest gate.

    The remote batch is immutable and already terminal. Recovery keeps its batch_id,
    reuses the SHA-verified local archive when present, and never creates a new batch.
    A conditional status update makes overlapping startup scans safe.
    """

    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import get_connection

    signature = "result manifest fields do not match GPU Control V2"
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, status, output_dir, options_json
            FROM comfyui_runs
            WHERE status = 'FAILED' AND options_json LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (f"%{signature}%", max(1, min(int(limit), 200))),
        ).fetchall()

    recovered: list[str] = []
    failed: list[dict[str, str]] = []
    skipped: list[str] = []
    for row in rows:
        run_id = str(row["id"])
        try:
            options = json.loads(row["options_json"] or "{}")
            remote = dict(options.get("gpu_control") or {})
            if str(remote.get("status") or "").upper() != "SUCCEEDED":
                skipped.append(run_id)
                continue
            result_zip = Path(settings.storage_dir) / "gpu_control_batches" / run_id / "result.zip"
            if not result_zip.is_file():
                skipped.append(run_id)
                continue

            parent_status_path = Path(str(row["output_dir"] or "")).parent / "status.json"
            if not parent_status_path.is_file():
                skipped.append(run_id)
                continue
            parent = json.loads(parent_status_path.read_text(encoding="utf-8"))
            current_child = str((parent.get("children") or {}).get("comfyui_run_id") or "")
            parent_status = str(parent.get("status") or "").upper()
            if current_child != run_id or parent_status in {"DONE", "DONE_WITH_ERRORS", "CANCELED"}:
                skipped.append(run_id)
                continue

            with get_connection() as conn:
                claimed = conn.execute(
                    "UPDATE comfyui_runs SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                    ("QUEUED", _now(), run_id, "FAILED"),
                ).rowcount
            if claimed != 1:
                skipped.append(run_id)
                continue

            _run_gpu_control_worker(run_id)
            latest = _get_run(run_id)
            if latest and str(latest["status"] or "").upper() == "DONE":
                recovered.append(run_id)
            else:
                latest_options = json.loads(latest["options_json"] or "{}") if latest else {}
                latest_error = str((latest_options.get("gpu_control") or {}).get("client_error") or "revalidation failed")
                failed.append({"run_id": run_id, "error": latest_error})
        except Exception as exc:
            failed.append({"run_id": run_id, "error": str(exc)})

    return {"ok": not failed, "recovered": recovered, "failed": failed, "skipped": skipped}


def _run_gpu_control_worker(run_id: str) -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.services.gpu_control_batch import (
        GpuControlBatchClient,
        GpuControlError,
        TERMINAL_BATCH_STATUSES,
        build_input_batch,
        compact_remote_state,
        merge_remote_state,
        result_artifact,
        validate_partial_failed_items,
        validate_workflow_identity,
        verify_and_publish_result,
    )

    try:
        row = _get_run(run_id)
        if not row:
            return
        options = json.loads(row["options_json"] or "{}")
        files = [Path(path) for path in json.loads(row["files_json"] or "[]")]
        src = Path(row["input_dir"])
        dst = Path(row["output_dir"])
        prompt_ids = json.loads(row["prompt_ids_json"] or "[]")
        workspace = Path(settings.storage_dir) / "gpu_control_batches" / run_id
        remote_state = dict(options.get("gpu_control") or {})
        remote_state.setdefault("trace_id", str(options.get("trace_id") or f"assetclaw-{run_id.lower()}"))
        _mark_gpu_stage(remote_state, "prepare_started_at")
        prepare_started = time.perf_counter()
        options["gpu_control"] = remote_state
        _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
        _trace_gpu_stage(run_id, remote_state, "prepare_started")
        prepared = build_input_batch(
            run_id,
            src,
            files,
            workspace,
            preserve_structure=bool(options.get("preserve_structure", True)),
            external_batch_id=str(options.get("external_batch_id") or "") or None,
            parameters=dict(options.get("cluster_parameters") or {}),
        )
        _mark_gpu_stage(remote_state, "prepare_finished_at")
        remote_state["client_prepare_ms"] = round((time.perf_counter() - prepare_started) * 1000, 3)
        remote_state.update(
            {
                "external_batch_id": prepared["external_batch_id"],
                "idempotency_key": prepared["idempotency_key"],
                "manifest_sha256": prepared["manifest_sha256"],
                "archive_path": prepared["archive_path"],
                "manifest_path": prepared["manifest_path"],
                "status": str(remote_state.get("status") or "PREPARING"),
            }
        )
        options["gpu_control"] = remote_state
        _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
        _trace_gpu_stage(run_id, remote_state, "prepare_finished", duration_ms=remote_state["client_prepare_ms"])
        client = GpuControlBatchClient()
        batch_id = str(remote_state.get("batch_id") or "")
        if not batch_id:
            _wait_for_gpu_control_admission(run_id, client, options, remote_state, prompt_ids)
            latest = _get_run(run_id)
            if not latest or latest["status"] == "CANCELED":
                return
            _mark_gpu_stage(remote_state, "create_started_at")
            create_started = time.perf_counter()
            _trace_gpu_stage(run_id, remote_state, "create_started", request_id=f"{run_id.lower()}-create-01")
            created = client.create_batch(
                Path(prepared["archive_path"]),
                prepared["manifest"],
                idempotency_key=prepared["idempotency_key"],
                request_id=f"{run_id.lower()}-create-01",
            )
            returned_external_id = str(created.get("external_batch_id") or "")
            if returned_external_id and returned_external_id != prepared["external_batch_id"]:
                raise GpuControlError("GPU Control returned a mismatched external_batch_id")
            identity_validation = validate_workflow_identity(created)
            remote_state = merge_remote_state(remote_state, created)
            remote_state["identity_validation"] = identity_validation
            _mark_gpu_stage(remote_state, "create_finished_at")
            remote_state["client_create_ms"] = round((time.perf_counter() - create_started) * 1000, 3)
            remote_state["create_response_meta"] = dict(created.get("_response_meta") or {})
            batch_id = str(remote_state.get("batch_id") or "")
            if not batch_id:
                raise GpuControlError("GPU Control create response has no batch_id")
            options["gpu_control"] = remote_state
            _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
            _trace_gpu_stage(run_id, remote_state, "create_finished", duration_ms=remote_state["client_create_ms"])
            latest = _get_run(run_id)
            if not latest or latest["status"] == "CANCELED":
                latest_options = json.loads(latest["options_json"] or "{}") if latest else options
                latest_remote = dict(latest_options.get("gpu_control") or remote_state)
                cancel_intent = dict(latest_remote.get("cancel_intent") or {})
                if cancel_intent:
                    try:
                        cancelled = client.cancel_batch(
                            batch_id,
                            idempotency_key=str(cancel_intent.get("idempotency_key") or f"{prepared['idempotency_key']}:cancel"),
                            request_id=str(cancel_intent.get("request_id") or f"{run_id.lower()}-cancel-after-create-01"),
                        )
                        latest_remote = merge_remote_state(latest_remote, cancelled)
                        cancel_intent["accepted_at"] = _now()
                        cancel_intent["remote_status"] = str(cancelled.get("status") or "")
                        latest_remote["cancel_intent"] = cancel_intent
                        latest_options["gpu_control"] = latest_remote
                        _save_run_progress(run_id, prompt_ids, latest_options)
                    except Exception as exc:
                        latest_remote["cancel_submit_error"] = str(exc)
                        latest_options["gpu_control"] = latest_remote
                        _save_run_progress(run_id, prompt_ids, latest_options)
                return

        started = time.monotonic()
        last_remote_change = started
        previous_signature = (
            str(remote_state.get("status") or ""),
            float(remote_state.get("progress") or 0),
            json.dumps(remote_state.get("counts") or {}, sort_keys=True),
        )
        consecutive_poll_errors = 0
        watchdog_reported = bool(remote_state.get("watchdog_exceeded_at"))
        _mark_gpu_stage(remote_state, "polling_started_at")
        _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
        while True:
            latest = _get_run(run_id)
            if not latest or latest["status"] == "CANCELED":
                return
            elapsed_seconds = time.monotonic() - started
            watchdog_seconds = int(settings.gpu_control_execution_timeout_seconds or 86400)
            if elapsed_seconds > watchdog_seconds and not watchdog_reported:
                watchdog_reported = True
                remote_state["watchdog_exceeded_at"] = _now()
                remote_state["watchdog_elapsed_seconds"] = round(elapsed_seconds, 3)
                remote_state["watchdog_action"] = "OBSERVE_AND_RECONCILE_WITHOUT_CANCEL"
                _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
                _trace_gpu_stage(
                    run_id,
                    remote_state,
                    "watchdog_exceeded_without_cancel",
                    elapsed_seconds=round(elapsed_seconds, 3),
                )
                _notify(
                    run_id,
                    "GPU Control 批次超过本地观察阈值；远端仍是权威状态，"
                    "动画管家会继续使用原 batch_id 查询，不会自动取消或改走本机。",
                )
            poll_sequence = int(remote_state.get("poll_sequence") or 0) + 1
            remote_state["poll_sequence"] = poll_sequence
            poll_request_id = f"{run_id.lower()}-poll-{poll_sequence:06d}"
            try:
                payload = client.get_batch(
                    batch_id,
                    request_id=poll_request_id,
                )
                consecutive_poll_errors = 0
            except Exception as exc:
                consecutive_poll_errors += 1
                remote_state["poll_error"] = str(exc)
                remote_state["poll_error_count"] = consecutive_poll_errors
                remote_state["poll_errors_total"] = int(remote_state.get("poll_errors_total") or 0) + 1
                remote_state["last_poll_error_at"] = _now()
                remote_state["last_poll_request_id"] = poll_request_id
                _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
                poll_error_limit = max(3, int(settings.gpu_control_poll_error_limit or 20))
                if consecutive_poll_errors == poll_error_limit:
                    _notify(
                        run_id,
                        f"GPU Control 状态查询已连续失败 {consecutive_poll_errors} 次；"
                        "远端状态不明确，继续用原 batch_id 恢复查询，不会改走本机。",
                    )
                retry_delays = (1, 2, 4, 8, 15, 30)
                time.sleep(retry_delays[min(consecutive_poll_errors - 1, len(retry_delays) - 1)])
                continue

            returned_batch_id = str(payload.get("batch_id") or "")
            returned_external_id = str(payload.get("external_batch_id") or "")
            if returned_batch_id and returned_batch_id != batch_id:
                raise GpuControlError("GPU Control status returned a mismatched batch_id")
            if returned_external_id and returned_external_id != prepared["external_batch_id"]:
                raise GpuControlError("GPU Control status returned a mismatched external_batch_id")
            identity_validation = validate_workflow_identity(payload)
            remote_state = merge_remote_state(remote_state, payload)
            remote_state["identity_validation"] = identity_validation
            observed_at = _now()
            remote_state["last_poll_succeeded_at"] = observed_at
            remote_state["last_poll_request_id"] = poll_request_id
            remote_state.pop("poll_error", None)
            remote_state.pop("poll_error_count", None)
            current_signature = (
                str(remote_state.get("status") or ""),
                float(remote_state.get("progress") or 0),
                json.dumps(remote_state.get("counts") or {}, sort_keys=True),
            )
            if current_signature != previous_signature:
                previous_signature = current_signature
                last_remote_change = time.monotonic()
                remote_state["last_remote_change_at"] = observed_at
            remote_state["idle_gap_seconds"] = round(time.monotonic() - last_remote_change, 3)
            _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
            remote_status = str(remote_state.get("status") or "").upper()
            if remote_status not in TERMINAL_BATCH_STATUSES:
                time.sleep(max(1, int(settings.gpu_control_poll_interval_seconds or 3)))
                continue
            _mark_gpu_stage(remote_state, "remote_terminal_observed_at")
            remote_state["remote_terminal_request_id"] = poll_request_id
            _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
            _trace_gpu_stage(run_id, remote_state, "remote_terminal", remote_status=remote_status)
            if remote_status == "CANCELLED":
                if not remote_state.get("cancel_intent"):
                    remote_state["illegal_remote_cancelled"] = {
                        "detected_at": _now(),
                        "request_id": poll_request_id,
                        "reason": "remote_cancelled_without_local_cancel_intent",
                    }
                    _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
                    raise GpuControlError("GPU Control returned CANCELLED without a persisted local cancel intent")
                _set_run_status(run_id, "CANCELED")
                return
            counts = dict(remote_state.get("counts") or {})
            partial_artifacts = [
                item
                for item in remote_state.get("artifacts") or []
                if str(item.get("kind") or "") == "result_archive"
            ]
            if (
                remote_status == "PARTIAL_SUCCESS"
                and int(counts.get("succeeded") or 0) > 0
                and int(counts.get("failed") or 0) > 0
                and partial_artifacts
            ):
                artifact = result_artifact(remote_state)
                result_zip = workspace / "partial-result.zip"
                download = client.download_artifact(
                    artifact,
                    result_zip,
                    request_id=f"{run_id.lower()}-partial-download-01",
                )
                published = verify_and_publish_result(
                    result_zip,
                    str(artifact.get("sha256") or download["sha256"]),
                    prepared,
                    dst,
                    run_id,
                    strict_frame_identity=bool(options.get("strict_frame_identity")),
                    preserve_existing=True,
                    expected_batch_id=batch_id,
                    expected_external_batch_id=prepared["external_batch_id"],
                    allow_partial=True,
                    partial_status=remote_state,
                )
                completed_at = _now()
                published_ordinals = {int(item["ordinal"]) for item in published}
                remote_failed = validate_partial_failed_items(remote_state, prepared, published_ordinals)
                failed_items = []
                remote_failed_by_ordinal = {
                    int(item.get("ordinal")): item
                    for item in remote_failed
                    if isinstance(item, dict) and item.get("ordinal") is not None
                }
                for expected in prepared.get("frames") or []:
                    ordinal = int(expected["ordinal"])
                    if ordinal in published_ordinals:
                        continue
                    detail = remote_failed_by_ordinal.get(ordinal, {})
                    failed_items.append(
                        {
                            "ordinal": ordinal,
                            "src_path": expected["source_path"],
                            "rel_path": expected["output_relative_path"],
                            "dst_path": str(dst / Path(*PurePosixPath(expected["output_relative_path"]).parts)),
                            "error": str(detail.get("message") or detail.get("error") or remote_state.get("error") or "GPU frame failed"),
                            "error_kind": str(detail.get("code") or "REMOTE_FRAME_FAILED"),
                            "node_id": str(detail.get("node_id") or ""),
                            "attempts": int(detail.get("attempts") or 0),
                            "attempted_node_ids": list(detail.get("attempted_node_ids") or []),
                            "failed_at": completed_at,
                        }
                    )
                options["prompt_map"] = [
                    *[{**item, "completed_at": completed_at} for item in published],
                    *failed_items,
                ]
                remote_state["partial_result"] = {
                    "published": len(published),
                    "failed": len(failed_items),
                    "archive_path": str(result_zip),
                    "archive_sha256": download["sha256"],
                    "published_at": completed_at,
                }
                options["gpu_control"] = remote_state
                _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
                _set_run_status(run_id, "DONE_WITH_ERRORS")
                _trace_gpu_stage(
                    run_id,
                    remote_state,
                    "partial_result_published",
                    published=len(published),
                    failed=len(failed_items),
                )
                return
            if remote_status != "SUCCEEDED":
                raise GpuControlError(
                    f"GPU Control batch ended as {remote_status}: {remote_state.get('error') or 'no error detail'}"
                )

            if int(counts.get("total") or -1) != len(files) or int(counts.get("succeeded") or -1) != len(files):
                raise GpuControlError(f"GPU Control SUCCEEDED counts do not match the submitted total: {counts}")
            for count_name in ("pending", "queued", "running", "failed", "cancelled"):
                if int(counts.get(count_name) or 0) != 0:
                    raise GpuControlError(f"GPU Control SUCCEEDED response contains unfinished frames: {counts}")
            artifact = result_artifact(payload)
            result_zip = workspace / "result.zip"
            _mark_gpu_stage(remote_state, "download_started_at")
            download_started = time.perf_counter()
            _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
            _trace_gpu_stage(run_id, remote_state, "download_started", request_id=f"{run_id.lower()}-download-01")
            download = client.download_artifact(
                artifact,
                result_zip,
                request_id=f"{run_id.lower()}-download-01",
            )
            _mark_gpu_stage(remote_state, "download_finished_at")
            remote_state["client_download_ms"] = round((time.perf_counter() - download_started) * 1000, 3)
            latest = _get_run(run_id)
            if not latest or latest["status"] == "CANCELED":
                return
            _mark_gpu_stage(remote_state, "publish_started_at")
            publish_started = time.perf_counter()
            published = verify_and_publish_result(
                result_zip,
                str(artifact.get("sha256") or download["sha256"]),
                prepared,
                dst,
                run_id,
                strict_frame_identity=bool(options.get("strict_frame_identity")),
                preserve_existing=bool(options.get("skip_existing")),
                expected_batch_id=batch_id,
                expected_external_batch_id=prepared["external_batch_id"],
            )
            completed_at = _now()
            _mark_gpu_stage(remote_state, "publish_finished_at")
            remote_state["client_publish_ms"] = round((time.perf_counter() - publish_started) * 1000, 3)
            options["prompt_map"] = [{**item, "completed_at": completed_at} for item in published]
            remote_state["result_archive_path"] = str(result_zip)
            remote_state["result_archive_sha256"] = download["sha256"]
            remote_state["result_download"] = download
            remote_state["published_at"] = completed_at
            remote_state.pop("client_error", None)
            remote_state.pop("failed_at", None)
            _mark_gpu_stage(remote_state, "completed_at")
            options["gpu_control"] = remote_state
            _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)
            _set_run_status(run_id, "DONE")
            _trace_gpu_stage(
                run_id,
                remote_state,
                "completed",
                download_ms=remote_state["client_download_ms"],
                publish_ms=remote_state["client_publish_ms"],
                download_attempts=download.get("attempts"),
                resumed_from_bytes=download.get("resumed_from_bytes"),
            )
            return
    except Exception as exc:
        row = _get_run(run_id)
        if row and row["status"] != "CANCELED":
            options = json.loads(row["options_json"] or "{}")
            remote_state = dict(options.get("gpu_control") or {})
            remote_state["client_error"] = str(exc)
            remote_state["failed_at"] = _now()
            options["gpu_control"] = remote_state
            _save_gpu_control_progress(
                run_id,
                json.loads(row["prompt_ids_json"] or "[]"),
                options,
                remote_state,
            )
            _set_run_status(run_id, "FAILED")
            _trace_gpu_stage(run_id, remote_state, "failed", error=str(exc))
            _notify(run_id, f"GPU Control 抠图批任务失败：{exc}")
    finally:
        _WORKER_RUNS.discard(run_id)


def _wait_for_gpu_control_admission(
    run_id: str,
    client,
    options: dict[str, Any],
    remote_state: dict[str, Any],
    prompt_ids: list[str],
) -> None:
    """Record scheduler telemetry without blocking server-side queue admission.

    The V3 capacity endpoint is advisory.  ``create_batch`` is the authoritative
    admission operation and the server owns queueing, so a saturated suggestion
    (or a failed capacity probe) must never strand a remote-routed task locally.
    """

    if not hasattr(client, "scheduler_capacity"):
        return
    sequence = int(remote_state.get("admission_poll_sequence") or 0)
    sequence += 1
    try:
        capacity = client.scheduler_capacity(request_id=f"{run_id.lower()}-admission-{sequence:06d}")
        supported = bool(capacity.get("supported"))
        accepting = capacity.get("accepting_batches") is True
        suggested = capacity.get("suggested_max_new_batches")
        admission = {
            "status": "SUBMITTING",
            "policy": "server_queue_authoritative",
            "supported": supported,
            "accepting_batches": capacity.get("accepting_batches"),
            "queue_depth": capacity.get("queue_depth"),
            "active_batches": capacity.get("active_batches"),
            "idle_nodes": capacity.get("idle_nodes"),
            "suggested_max_new_batches": suggested,
            "updated_at": _now(),
        }
        if supported and not accepting:
            admission["note"] = "capacity advisory is saturated; create_batch still owns queue admission"
    except Exception as exc:
        admission = {
            "status": "SUBMITTING",
            "policy": "server_queue_authoritative",
            "supported": False,
            "probe_error": str(exc),
            "updated_at": _now(),
        }
    remote_state["admission_poll_sequence"] = sequence
    remote_state["admission"] = admission
    options["gpu_control"] = remote_state
    _save_gpu_control_progress(run_id, prompt_ids, options, remote_state)


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
        final_save_image_node_id = _final_save_image_node_id(base_workflow)
        done_sources = {
            item.get("src_path")
            for item in prompt_map
            if not item.get("error") and Path(str(item.get("dst_path") or "")).is_file()
        }

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
            uploaded = ""
            try:
                attempts = max(1, int(options.get("output_validation_retries", 3)))
                retry_delay = max(5, int(options.get("output_validation_retry_delay_seconds", 20)))
                last_error: Exception | None = None
                upload_verification: dict[str, str] = {}
                identity_verification: dict[str, Any] = {}
                processing_seconds = 0.0
                for attempt in range(1, attempts + 1):
                    attempt_started = time.monotonic()
                    try:
                        if settings.comfyui_fake_mode:
                            _fake_copy_image(image_path, target)
                        else:
                            uploaded = comfyui_client.upload_image(
                                image_path,
                                remote_name=_unique_upload_name(run_id, src, image_path),
                            )
                            upload_verification = comfyui_client.verify_uploaded_image(image_path, uploaded)
                            workflow = copy.deepcopy(base_workflow)
                            if options.get("input_node_id"):
                                workflow = patch_node_input(workflow, str(options["input_node_id"]), str(options.get("input_name") or "image"), uploaded)
                            else:
                                workflow = patch_load_image(workflow, uploaded)
                            prompt = prepare_api_prompt_for_run(
                                workflow,
                                auxiliary_output_root=str(settings.comfyui_dir / "output" / "assetclaw_aux" / run_id),
                            )
                            prompt_id = comfyui_client.submit_prompt(prompt, client_id=run_id)
                            prompt_ids.append(prompt_id)
                            _save_run_progress(run_id, prompt_ids, options)
                            history = comfyui_client.wait_for_completion(prompt_id)
                            output = resolve_best_output(
                                history,
                                prompt_id,
                                local_path_resolver=comfyui_client.resolve_local_output_path,
                                final_save_image_node_id=final_save_image_node_id,
                            )
                            comfyui_client.download_output(output["filename"], output.get("subfolder", ""), output.get("type", "output"), target)
                        if not settings.comfyui_fake_mode:
                            _ensure_final_transparent_png(target)
                            if options.get("strict_frame_identity"):
                                from assetclaw_matting.skills.sequence_integrity import validate_matte_identity

                                identity_verification = validate_matte_identity(image_path, target)
                        last_error = None
                        processing_seconds = max(0.0, time.monotonic() - attempt_started)
                        break
                    except Exception as exc:
                        last_error = exc
                        if target.exists():
                            target.unlink()
                        if isinstance(exc, TimeoutError):
                            raise
                        if _is_non_retryable_prompt_error(exc):
                            raise RuntimeError(
                                f"ComfyUI workflow/runtime incompatibility; retry disabled: {exc}"
                            ) from exc
                        if attempt < attempts:
                            _notify(
                                run_id,
                                "ComfyUI 输出校验失败，已拒绝写入 matte；等待缓存稳定后重跑当前帧："
                                f"\n帧：{image_path.name}"
                                f"\n重试：{attempt + 1}/{attempts}"
                                f"\n等待：{retry_delay}s"
                                f"\n原因：{exc}",
                            )
                            time.sleep(retry_delay)
                            continue
                        raise RuntimeError(f"ComfyUI 输出校验失败，已重试 {attempts} 次，拒绝写入 matte：{image_path.name}\n{last_error}") from exc
                prompt_map = _replace_prompt_result(prompt_map, str(image_path), {
                    "prompt_id": prompt_id,
                    "src_path": str(image_path),
                    "rel_path": _relative_output_key(src, image_path, bool(options.get("preserve_structure", True))),
                    "dst_path": str(target),
                    **upload_verification,
                    "identity_verification": identity_verification,
                    "duration_seconds": round(processing_seconds, 3),
                    "completed_at": _now(),
                })
                options["prompt_map"] = prompt_map
                _save_run_progress(run_id, prompt_ids, options)
            except Exception as exc:
                error_detail = _execution_failure_detail(exc)
                prompt_map = _replace_prompt_result(prompt_map, str(image_path), {
                    "prompt_id": prompt_id,
                    "src_path": str(image_path),
                    "rel_path": _relative_output_key(src, image_path, bool(options.get("preserve_structure", True))),
                    "dst_path": str(target),
                    "error": str(exc),
                    "error_kind": str(error_detail.get("kind") or ""),
                    "error_detail": error_detail,
                    "failed_at": _now(),
                })
                options["prompt_map"] = prompt_map
                _save_run_progress(run_id, prompt_ids, options)
                _set_run_status(run_id, "FAILED")
                _notify(
                    run_id,
                    "ComfyUI 抠图出错，已停止，未写入中间图："
                    f"\n问题图片：{image_path}"
                    f"\n目标输出：{target}"
                    f"\n原因：{exc}",
                )
                return
            finally:
                if uploaded:
                    comfyui_client.cleanup_uploaded_image(uploaded)
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
                if status.get("backend") == "local" and not status.get("fake_mode") and status.get("completed", 0):
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


def _collect_images(root: Path, recursive: bool, max_images: int, priority_characters: list[str] | None = None) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [p for p in iterator if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    priority = {_safe_task_name(item): index for index, item in enumerate(priority_characters or []) if str(item or "").strip()}

    def sort_key(path: Path) -> tuple[int, str]:
        rel = str(path.relative_to(root)).replace("\\", "/").lower()
        first = rel.split("/", 1)[0]
        character = first.split("-", 1)[0]
        return priority.get(character, len(priority)), rel

    files = sorted(files, key=sort_key)
    return files[: max(1, min(max_images, 10000))]


def _final_save_image_node_id(workflow: dict[str, Any]) -> str:
    node_id = find_primary_save_image_node_id(workflow)
    if not node_id:
        raise ValueError("当前 ComfyUI workflow 没有找到 SaveImage/保存图像 节点，拒绝运行。")
    return node_id


def _safe_task_name(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "_", str(value).strip()).strip("_").lower()


def _relative_output_key(input_root: Path, image_path: Path, preserve_structure: bool) -> str:
    rel = image_path.relative_to(input_root) if preserve_structure else Path(image_path.name)
    return str(rel.with_suffix(".png"))


def _output_target(input_root: Path, output_root: Path, image_path: Path, preserve_structure: bool) -> Path:
    return output_root / _relative_output_key(input_root, image_path, preserve_structure)


def _unique_upload_name(run_id: str, input_root: Path, image_path: Path) -> str:
    relative = str(image_path.relative_to(input_root)).replace("\\", "/")
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
    safe_rel = re.sub(r"[^0-9A-Za-z._-]+", "_", relative).strip("._-")
    return f"assetclaw_{run_id}_{digest}_{safe_rel}"


def _fake_copy_image(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        with Image.open(src) as image:
            image.convert("RGBA").save(dst, "PNG")
    except Exception:
        shutil.copy2(src, dst)


def _ensure_final_transparent_png(path: Path) -> None:
    quality = inspect_local_png(path)
    if quality.get("valid"):
        return
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    size = f"{quality.get('width', 0)}x{quality.get('height', 0)}"
    alpha = f"{quality.get('alpha_min')}..{quality.get('alpha_max')}" if quality.get("has_alpha") else "none"
    raise ValueError(
        "输出不是合格的最终透明 PNG，已删除并拒绝进入 matte："
        f"{path}；size={size}；mode={quality.get('mode') or '?'}；"
        f"alpha={alpha}；reason={quality.get('reason')}"
    )


def _last_completed_name(prompt_map: list[dict[str, str]]) -> str:
    completed = [Path(str(item.get("src_path") or item.get("dst_path") or "")).name for item in prompt_map if Path(str(item.get("dst_path") or "")).exists()]
    return completed[-1] if completed else ""


def _last_completed_rel_path(prompt_map: list[dict[str, str]]) -> str:
    completed = [str(item.get("rel_path") or "") for item in prompt_map if Path(str(item.get("dst_path") or "")).exists()]
    return completed[-1] if completed else ""


def _last_error_summary(error_items: list[dict[str, Any]]) -> str:
    if not error_items:
        return ""
    item = error_items[-1]
    frame = Path(str(item.get("src_path") or item.get("rel_path") or "")).name
    error = _compact_error(str(item.get("error") or ""))
    return f"{frame}: {error}" if frame else error


_LOCAL_GPU_OOM_MARKERS = (
    "torch.outofmemoryerror",
    "cuda out of memory",
    "cuda error: out of memory",
    "cudnn_status_alloc_failed",
    "cublas_status_alloc_failed",
    "allocation on device",
    "ran out of memory",
)


_NON_RETRYABLE_PROMPT_MARKERS = (
    "missing_node_type",
    "node 'int' not found",
    "node type not found",
    "unknown node type",
)


def _is_non_retryable_prompt_error(exc: BaseException) -> bool:
    """Return true for deterministic workflow validation/submission errors."""

    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    folded = "\n".join(str(item) for item in chain).casefold()
    return any(marker in folded for marker in _NON_RETRYABLE_PROMPT_MARKERS)


def _execution_failure_detail(exc: BaseException) -> dict[str, str]:
    """Preserve a compact, structured failure classification before UI truncation."""

    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = "\n".join(f"{type(item).__name__}: {item}" for item in chain)
    folded = text.casefold()
    if any(marker in folded for marker in _LOCAL_GPU_OOM_MARKERS):
        kind = "GPU_OOM"
    elif any(marker in folded for marker in _NON_RETRYABLE_PROMPT_MARKERS):
        kind = "WORKFLOW_INCOMPATIBLE"
    else:
        kind = "EXECUTION_ERROR"
    exception_type = ""
    match = re.search(r"exception_type['\"]?\s*[:=]\s*['\"]([^'\"]+)", text, flags=re.IGNORECASE)
    if match:
        exception_type = match.group(1).strip()
    elif chain:
        exception_type = type(chain[-1]).__name__
    node_id = ""
    match = re.search(r"node_id['\"]?\s*[:=]\s*['\"]([^'\"]+)", text, flags=re.IGNORECASE)
    if match:
        node_id = match.group(1).strip()
    return {
        "kind": kind,
        "exception_type": exception_type,
        "node_id": node_id,
        "message": _compact_error(text, limit=1200),
    }


def _error_item_summaries(error_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "frame": Path(str(item.get("src_path") or item.get("rel_path") or "")).name,
            "error": _compact_error(str(item.get("error") or "")),
            "ordinal": item.get("ordinal"),
            "error_kind": str(item.get("error_kind") or ""),
            "node_id": str(item.get("node_id") or ""),
            "attempts": int(item.get("attempts") or 0),
            "attempted_node_ids": list(item.get("attempted_node_ids") or []),
        }
        for item in error_items
    ]


def _compact_error(text: str, limit: int = 400) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:limit] + ("..." if len(cleaned) > limit else "")


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


def _save_gpu_control_progress(
    run_id: str,
    prompt_ids: list[str],
    options: dict[str, Any],
    remote_state: dict[str, Any],
) -> None:
    """Persist worker progress without erasing a concurrent operator cancel intent."""

    latest = _get_run(run_id)
    try:
        latest_options_json = latest["options_json"] if latest else None
    except (KeyError, IndexError, TypeError):
        latest_options_json = None
    if latest_options_json is not None:
        latest_options = json.loads(latest_options_json or "{}")
        latest_remote = dict(latest_options.get("gpu_control") or {})
        merged_remote = dict(latest_remote)
        merged_remote.update(remote_state)
        for protected_key in ("cancel_intent", "cancel_requested_at", "cancel_response_meta"):
            if latest_remote.get(protected_key):
                merged_remote[protected_key] = latest_remote[protected_key]
        merged_options = dict(latest_options)
        merged_options["prompt_map"] = list(options.get("prompt_map") or latest_options.get("prompt_map") or [])
        merged_options["gpu_control"] = merged_remote
        options.clear()
        options.update(merged_options)
        remote_state.clear()
        remote_state.update(merged_remote)
    else:
        options["gpu_control"] = remote_state
    _save_run_progress(run_id, prompt_ids, options)


def _looks_stalled(row: Any, stale_seconds: int = 60) -> bool:
    try:
        updated_at = datetime.fromisoformat(row["updated_at"])
    except Exception:
        return True
    return time.time() - updated_at.timestamp() >= stale_seconds


def _eta(elapsed: float, completed: int, total: int) -> int | None:
    if completed <= 0 or total <= completed:
        return None
    avg = elapsed / completed
    return int(avg * (total - completed))


def _replace_prompt_result(
    prompt_map: list[dict[str, Any]],
    source_path: str,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    return [item for item in prompt_map if str(item.get("src_path") or "") != source_path] + [result]


def _eta_from_prompt_map(
    prompt_map: list[dict[str, Any]],
    total: int,
    created_at: str,
) -> int | None:
    successful = [
        item
        for item in prompt_map
        if not item.get("error") and Path(str(item.get("dst_path") or "")).is_file()
    ]
    completed = len(successful)
    if completed <= 0 or total <= completed:
        return None
    durations = [
        float(item.get("duration_seconds"))
        for item in successful
        if isinstance(item.get("duration_seconds"), (int, float)) and float(item["duration_seconds"]) > 0
    ]
    if not durations:
        try:
            started = datetime.fromisoformat(created_at).timestamp()
            mtimes = sorted(Path(str(item["dst_path"])).stat().st_mtime for item in successful)
            previous = started
            for mtime in mtimes:
                duration = mtime - previous
                if 1 <= duration <= 3600:
                    durations.append(duration)
                previous = mtime
        except (KeyError, OSError, TypeError, ValueError):
            durations = []
    if not durations:
        return None
    recent = sorted(durations[-10:])
    midpoint = len(recent) // 2
    median = recent[midpoint] if len(recent) % 2 else (recent[midpoint - 1] + recent[midpoint]) / 2
    return max(0, int(median * (total - completed)))


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"

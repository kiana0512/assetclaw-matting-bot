from __future__ import annotations

import json
import hashlib
import os
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from assetclaw_matting.config import settings
from assetclaw_matting.runtime_context import get_runtime_context
from assetclaw_matting.skills import matting_pipeline_skills
from assetclaw_matting.skills.media_skills import IMAGE_EXTS
from assetclaw_matting.skills.security import validate_path


RUNS_ROOT = Path(settings.storage_dir) / "direct_image_runs"
FINISHED = {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}
MAX_FULL_PIPELINE_RETRIES = 2
MAX_PARTIAL_MATTING_REPAIRS = 2
MAX_SOURCE_RECOVERY_CANDIDATES = 32
MAX_SOURCE_RECOVERY_ZIP_MEMBER_BYTES = 128 * 1024 * 1024
_WORKERS: set[str] = set()
_RUN_CONTEXT = threading.local()


def start(
    image_paths: list[str],
    source_names: list[str] | None = None,
    workflow_path: str | None = None,
    notify_interval_seconds: int = 60,
    run_label: str = "",
    package_as_sequence: bool = False,
    character_group_keys: list[str] | None = None,
    character_evidence: list[list[str]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not image_paths:
        raise ValueError("image_paths is required")
    images = [_validate_image(path) for path in image_paths]
    pipeline_notice = ""
    if not workflow_path and Path(settings.comfyui_workflow_path).name == settings.matting_pipeline_workflow_name:
        pipeline = matting_pipeline_skills.ensure_latest_for_task()
        if not pipeline.get("ok"):
            raise RuntimeError(str(pipeline.get("error") or "matting pipeline preflight failed"))
        workflow_path = str(pipeline.get("workflow_path") or "")
        pipeline_notice = str(pipeline.get("message") or "")
    names = list(source_names or [])
    group_keys = list(character_group_keys or [])
    evidence_sets = list(character_evidence or [])
    run_id = "IMG_" + uuid.uuid4().hex[:12].upper()
    run_dir = _active_runs_root() / run_id
    originals_dir = run_dir / "original_images"
    matte_dir = run_dir / "matte"
    smooth_dir = run_dir / "smooth"
    originals_dir.mkdir(parents=True, exist_ok=True)
    matte_dir.mkdir(parents=True, exist_ok=True)
    smooth_dir.mkdir(parents=True, exist_ok=True)

    items = []
    group_units: dict[str, str] = {}
    resolution_units: dict[str, dict[str, Any]] = {}
    for index, image in enumerate(images, start=1):
        name = _safe_name(names[index - 1] if index - 1 < len(names) else image.name)
        suffix = image.suffix if image.suffix.lower() in IMAGE_EXTS else ".png"
        image_dir = originals_dir / f"image_{index:02d}"
        image_dir.mkdir(parents=True, exist_ok=True)
        target = image_dir / f"{index:02d}_{Path(name).stem}{suffix}"
        shutil.copy2(image, target)
        width, height = _image_size(target)
        profile = _cherry_profile_from_dimensions(width, height)
        aspect = "square" if profile == "half" else "portrait"
        default_group = "sequence:default" if package_as_sequence else f"item:{index:04d}"
        group_key = str(group_keys[index - 1] if index - 1 < len(group_keys) else default_group)
        if group_key not in group_units:
            group_units[group_key] = f"{run_id}:image:{len(group_units) + 1:02d}"
        unit_id = group_units[group_key]
        supplied_evidence = evidence_sets[index - 1] if index - 1 < len(evidence_sets) else []
        unit = resolution_units.setdefault(
            group_key,
            {
                "unit_id": unit_id,
                "item_index": index,
                "group_key": group_key,
                "source_name": name,
                "evidence": [],
            },
        )
        for value in [*supplied_evidence, name, str(image)]:
            value_text = str(value or "").strip()
            if value_text and value_text not in unit["evidence"]:
                unit["evidence"].append(value_text)
        items.append(
            {
                "index": index,
                "item_id": f"{run_id}:image-item:{index:04d}",
                "character_unit_id": unit_id,
                "character_group_key": group_key,
                "source_path": str(image),
                "source_name": name,
                "source_evidence": [str(value) for value in supplied_evidence if str(value or "").strip()],
                "source_size_bytes": int(image.stat().st_size),
                "original_path": str(target),
                "name": target.name,
                "matte_dir": str(matte_dir / f"image_{index:02d}"),
                "smooth_dir": str(smooth_dir / f"image_{index:02d}"),
                "width": width,
                "height": height,
                "aspect": aspect,
                "cherry_profile": profile,
                "cherry_output_size": _cherry_output_size(profile),
                "matte_result_path": "",
                "postprocessed_result_path": "",
                "comparison_path": "",
                "result_path": "",
            }
        )

    ctx = get_runtime_context()
    run = {
        "id": run_id,
        "status": "RUNNING",
        "stage": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "run_label": run_label or f"{len(items)} 张图片",
        "package_as_sequence": bool(package_as_sequence or len(items) > 1),
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "conversation_id": ctx.get("conversation_id") or "",
        "user_id": (ctx.get("open_id") or ctx.get("user_id") or "") if ctx.get("channel") == "feishu" else "",
        "images": items,
        "children": {},
        "workflow_path": workflow_path or "",
        "pipeline_notice": pipeline_notice,
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds or 60), 3600)),
        "sent_files": [],
        "error": "",
        "log": [],
        "worker_pid": os.getpid(),
    }
    from assetclaw_matting.services.character_resolution import (
        bind_run_items,
        initialize_run_resolutions,
    )

    character_state = initialize_run_resolutions(
        run_kind="direct_image",
        run_id=run_id,
        run_dir=run_dir,
        conversation_id=str(run.get("conversation_id") or ""),
        chat_id=str(run.get("chat_id") or ""),
        user_id=str(run.get("user_id") or ""),
        units=resolution_units.values(),
    )
    bind_run_items("direct_image", run_id, items)
    run["character_question"] = str(character_state.get("prompt") or "")
    run["character_resolution"] = {
        "question_id": str(character_state.get("question_id") or ""),
        "total": len(character_state.get("items") or []),
        "pending": len(character_state.get("pending") or []),
    }
    _save(run)
    _start_worker(run_id)
    return {"ok": True, "run_id": run_id, **_public(run)}


def status(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    run = _reconcile_terminal_child(run)
    return {"ok": True, "run_id": run["id"], **_public(run)}


def list_runs(limit: int = 10, include_finished: bool = True, **_: Any) -> dict[str, Any]:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(RUNS_ROOT.glob("IMG_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        run = json.loads(path.read_text(encoding="utf-8"))
        run = _reconcile_terminal_child(run)
        if run.get("status") in FINISHED and not include_finished:
            continue
        items.append({"run_id": run["id"], **_public(run)})
        if len(items) >= max(1, min(int(limit), 50)):
            break
    return {"ok": True, "count": len(items), "items": items}


def recover_incomplete_runs() -> dict[str, Any]:
    """Close image parents orphaned by a Gateway or Feishu process restart."""
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    closed: list[str] = []
    still_running: list[str] = []
    waiting_character: list[str] = []
    for status_path in sorted(RUNS_ROOT.glob("IMG_*/status.json"), key=lambda item: item.stat().st_mtime):
        try:
            run = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        current_status = str(run.get("status") or "").upper()
        if current_status == "WAITING_CHARACTER":
            run_id = str(run.get("id") or status_path.parent.name)
            from assetclaw_matting.services.character_resolution import (
                all_run_units_frozen,
                mark_run_waiting,
                reconcile_pending_evidence,
                reconcile_resolved_units,
            )

            mark_run_waiting("direct_image", run_id, immediate_if_uninitialized=True)
            reconcile_pending_evidence("direct_image", run_id)
            reconcile_resolved_units("direct_image", run_id)
            if all_run_units_frozen("direct_image", run_id):
                resume_after_character_resolution(run_id)
                closed.append(run_id)
            else:
                waiting_character.append(run_id)
            continue
        if current_status == "FAILED":
            run_id = str(run.get("id") or status_path.parent.name)
            current_child_id = str((run.get("children") or {}).get("comfyui_run_id") or "")
            current_child = _comfyui_child_status(current_child_id)
            recovered_manifest_result = (
                "result manifest fields do not match" in str(run.get("error") or "").lower()
                and str(current_child.get("status") or "").upper() == "DONE"
            )
            if recovered_manifest_result:
                run["recovery_from_stage"] = "matting"
                run["status"] = "QUEUED"
                run["stage"] = "recovery_queued"
                run["worker_pid"] = 0
                run["error"] = ""
                _append_log(run, "GPU 结果已重新验收通过，从后处理继续，不重复提交 GPU 批次。")
                if _save(run, expected_statuses={"FAILED"}):
                    _start_recovery_worker(run_id)
                    closed.append(run_id)
                continue
        if current_status == "FAILED" and _should_auto_retry_failed_run(run):
            run_id = str(run.get("id") or status_path.parent.name)
            run["recovery_from_stage"] = "full_pipeline"
            run["status"] = "QUEUED"
            run["stage"] = "recovery_queued"
            run["worker_pid"] = 0
            _append_log(run, "检测到可自动恢复的失败，已进入有界完整流程恢复，不会直接向用户报失败。")
            if _save(run, expected_statuses={"FAILED"}):
                _start_recovery_worker(run_id)
                closed.append(run_id)
            continue
        if current_status not in {"RUNNING", "QUEUED", "PENDING"}:
            continue
        run = _reconcile_terminal_child(run)
        if str(run.get("status") or "").upper() in FINISHED:
            closed.append(str(run.get("id") or status_path.parent.name))
            continue
        run_id = str(run.get("id") or status_path.parent.name)
        worker_pid = int(run.get("worker_pid") or 0)
        local_worker = run_id in _WORKERS
        remote_worker = worker_pid > 0 and worker_pid != os.getpid() and _process_alive(worker_pid)
        if local_worker or remote_worker:
            still_running.append(run_id)
            continue
        run["recovery_from_stage"] = str(run.get("stage") or "")
        run["status"] = "QUEUED"
        run["stage"] = "recovery_queued"
        run["worker_pid"] = 0
        run["error"] = ""
        _append_log(run, "检测到图片直发执行进程已退出，保留远端 batch 并从持久化状态自动恢复。")
        _save(run)
        _start_recovery_worker(run_id)
        closed.append(run_id)
    return {"ok": True, "closed": closed, "still_running": still_running, "waiting_character": waiting_character}


def cancel(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    cancel_results = _cancel_child_runs(run)
    from assetclaw_matting.services.character_resolution import cancel_run_resolutions

    cancel_run_resolutions("direct_image", str(run["id"]))
    run["status"] = "CANCELED"
    run["stage"] = "canceled"
    run["character_question"] = ""
    run.setdefault("character_resolution", {})["pending"] = 0
    run["character_question"] = ""
    run.setdefault("character_resolution", {})["pending"] = 0
    run["character_question"] = ""
    run.setdefault("character_resolution", {})["pending"] = 0
    run.setdefault("children", {})["cancel_results"] = cancel_results
    run["updated_at"] = _now()
    _append_log(run, "用户请求取消任务。")
    if cancel_results:
        _append_log(run, "已同步取消子任务：" + "，".join(_child_cancel_label(item) for item in cancel_results))
    _save(run)
    return {"ok": True, "run_id": run["id"], "status": "CANCELED", "cancel_results": cancel_results, **_public(run)}


def resume_after_character_resolution(run_id: str) -> dict[str, Any]:
    from assetclaw_matting.services.character_resolution import all_run_units_frozen

    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct image run not found"}
    if not all_run_units_frozen("direct_image", run_id):
        return {"ok": False, "run_id": run_id, "status": run.get("status"), "error": "character resolution is incomplete"}
    if str(run.get("status") or "") != "WAITING_CHARACTER":
        return {"ok": True, "run_id": run_id, "status": run.get("status"), "scheduled": False}
    run["status"] = "QUEUED"
    run["stage"] = "character_resolved"
    run["recovery_from_stage"] = "postprocess"
    run["worker_pid"] = 0
    run["error"] = ""
    _append_log(run, "角色已全部确认，从已完成的抠图结果继续后处理；不会重复抠图。")
    if not _save(run, expected_statuses={"WAITING_CHARACTER"}):
        latest = _load(run_id) or run
        return {"ok": True, "run_id": run_id, "status": latest.get("status"), "scheduled": False}
    scheduled = _start_recovery_worker(run_id)
    return {"ok": True, "run_id": run_id, "status": "QUEUED", "scheduled": scheduled}


def fail_character_confirmation_timeout(run_id: str, reason: str) -> dict[str, Any]:
    """Terminally fail only a parent that is still at the character gate."""

    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct image run not found"}
    if str(run.get("status") or "") != "WAITING_CHARACTER":
        return {
            "ok": False,
            "run_id": run_id,
            "status": run.get("status"),
            "error": "direct image run is no longer waiting for character confirmation",
        }
    run["status"] = "FAILED"
    run["stage"] = "character_confirmation_timeout"
    run["error"] = str(reason or "角色确认超时")
    run["failure_kind"] = "character_confirmation_timeout"
    run["auto_retry_disabled"] = True
    run["character_question"] = ""
    run["worker_pid"] = 0
    resolution = run.setdefault("character_resolution", {})
    resolution["pending"] = 0
    resolution["expired"] = True
    run["updated_at"] = _now()
    _append_log(run, f"角色确认超时，任务已失败：{run['error']}")
    if not _save(run, expected_statuses={"WAITING_CHARACTER"}):
        latest = _load(run_id) or run
        return {
            "ok": False,
            "run_id": run_id,
            "status": latest.get("status"),
            "error": "direct image run changed while applying character timeout",
        }
    return {"ok": True, "run_id": run_id, "status": "FAILED", "stage": run["stage"]}


def _worker_once(run_id: str) -> None:
    run = _load(run_id)
    if not run:
        return
    try:
        _ensure_original_images(run)
        _mark(run, "RUNNING", "matting")
        _run_comfyui(run)
        if _is_canceled(run):
            return

        if not _prepare_character_gate(run):
            return

        _mark(run, "RUNNING", "postprocess")
        _run_cherry(run)
        if _is_canceled(run):
            return

        _mark(run, "RUNNING", "send")
        sent = _send_results(run)
        run["sent_files"] = sent
        run["status"] = "DONE"
        run["stage"] = "done"
        run["error"] = ""
        run["updated_at"] = _now()
        _append_log(run, f"结果文件发送完成：{len(sent)} 个")
        _save(run)
        plan = _cherry_plan_summary(run.get("images") or [])
        character_lines = _character_completion_lines(run.get("images") or [])
        suffix = f"，{plan}" if plan else ""
        if bool(run.get("package_as_sequence")) or len(run.get("images") or []) > 1:
            _notify(
                run,
                f"序列帧完成：{run['id']}，共 {len(run.get('images') or [])} 帧。"
                f"已按顺序返回透明抠图、后处理结果 2 个 ZIP{suffix}。{character_lines}",
            )
        else:
            _notify(run, f"图片完成：{run['id']}，已发回抠图、后处理、三联对比 3 份结果{suffix}。{character_lines}")
    except Exception as exc:
        if bool(getattr(_RUN_CONTEXT, "full_pipeline_retry_enabled", False)):
            _RUN_CONTEXT.failed_run = run
            raise
        run = _load(run_id) or run
        if run.get("status") != "CANCELED":
            run["status"] = "FAILED"
            run["error"] = str(exc)
            run["updated_at"] = _now()
            _close_failed_character_resolution(run)
            _append_log(run, f"任务失败：{exc}")
            _save(run)
            _notify(run, _user_failure_notice(run_id, exc))
    finally:
        latest = _load(run_id)
        if latest and int(latest.get("worker_pid") or 0) == os.getpid():
            latest["worker_pid"] = 0
            _save(latest)
        _WORKERS.discard(run_id)
        _restart_character_resume_if_needed(run_id)


def _worker(run_id: str) -> None:
    """Run the whole image pipeline with bounded, audited self-recovery."""

    _RUN_CONTEXT.full_pipeline_retry_enabled = True
    try:
        while True:
            try:
                _worker_once(run_id)
                return
            except Exception as exc:
                persisted = _load(run_id) or {}
                failed_run = getattr(_RUN_CONTEXT, "failed_run", {})
                run = _merge_runtime_delivery_state(persisted, failed_run)
                if not run or str(run.get("status") or "").upper() == "CANCELED":
                    return
                if _finish_after_confirmed_delivery(run):
                    return
                if _prepare_local_oom_gpu_fallback(run, exc):
                    _WORKERS.add(run_id)
                    continue
                if not _prepare_full_pipeline_retry(run, exc):
                    run["status"] = "FAILED"
                    run["stage"] = "recovery_exhausted"
                    run["error"] = str(exc)
                    run["updated_at"] = _now()
                    _close_failed_character_resolution(run)
                    _append_log(run, f"自动完整流程恢复已达到上限，任务失败：{exc}")
                    _save(run)
                    _notify(run, _user_failure_notice(run_id, exc, retries_exhausted=True))
                    return
                _WORKERS.add(run_id)
    finally:
        if hasattr(_RUN_CONTEXT, "full_pipeline_retry_enabled"):
            delattr(_RUN_CONTEXT, "full_pipeline_retry_enabled")
        if hasattr(_RUN_CONTEXT, "failed_run"):
            delattr(_RUN_CONTEXT, "failed_run")
        _WORKERS.discard(run_id)


_GPU_OOM_MARKERS = (
    "torch.outofmemoryerror",
    "cuda out of memory",
    "cuda error: out of memory",
    "cudnn_status_alloc_failed",
    "cublas_status_alloc_failed",
    "allocation on device",
    "ran out of memory",
)


def _is_local_gpu_oom(run: dict[str, Any], error: Exception | str) -> bool:
    """Return true only for a local ComfyUI GPU-memory failure during matting."""

    if str(run.get("stage") or "").strip().lower() != "matting":
        return False
    children = run.get("children") if isinstance(run.get("children"), dict) else {}
    child = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    selected_backend = str(
        child.get("backend") or run.get("matting_backend") or "local"
    ).strip().lower()
    if selected_backend not in {"local", "comfyui"}:
        return False
    if str(child.get("last_error_kind") or "").strip().upper() == "GPU_OOM":
        return True
    detail_payload = child.get("last_error_detail") if isinstance(child.get("last_error_detail"), dict) else {}
    if str(detail_payload.get("kind") or "").strip().upper() == "GPU_OOM":
        return True
    detail = " ".join(
        str(value or "")
        for value in (
            error,
            child.get("last_error"),
            child.get("error"),
            detail_payload.get("exception_type"),
            detail_payload.get("message"),
        )
    ).casefold()
    return any(marker in detail for marker in _GPU_OOM_MARKERS)


def _prepare_local_oom_gpu_fallback(run: dict[str, Any], error: Exception | str) -> bool:
    """Move one local OOM task to GPU Control without changing normal routing."""

    if not _is_local_gpu_oom(run, error):
        return False
    fallback = run.setdefault("local_oom_gpu_fallback", {})
    if bool(fallback.get("attempted")):
        return False

    fallback.update(
        {
            "attempted": True,
            "triggered_at": _now(),
            "from_backend": "local",
            "to_backend": "gpu_control",
            "trigger_stage": str(run.get("stage") or ""),
            "error": str(error),
        }
    )
    try:
        fallback["source_recovery"] = _ensure_original_images(run)
        _reset_outputs_for_full_retry(run)
    except Exception as recovery_error:
        fallback["preparation_error"] = str(recovery_error)
        _append_log(run, f"本机显存不足，但切换 GPU 集群前的原图恢复失败：{recovery_error}")
        _save(run)
        return False

    previous_children = run.get("children") if isinstance(run.get("children"), dict) else {}
    fallback["previous_comfyui_run_id"] = str(previous_children.get("comfyui_run_id") or "")
    run["children"] = {}
    run["matting_backend"] = "gpu_control"
    run["status"] = "QUEUED"
    run["stage"] = "local_oom_gpu_fallback_queued"
    run["error"] = ""
    run["worker_pid"] = os.getpid()
    for item in run.get("images") or []:
        for key in (
            "matte_result_path",
            "postprocessed_result_path",
            "comparison_path",
            "result_path",
            "cherry_sequence_group",
            "color_correction_run_id",
        ):
            item[key] = ""
        item["color_correction_status"] = "READY" if item.get("color_reference_path") else ""
        item["position_alignment_status"] = "READY" if item.get("position_alignment_enabled") else ""
        item["reference_postprocess_status"] = "READY" if item.get("color_reference_path") else ""
    _append_log(run, "检测到本机 4070 Ti 显存不足；本任务自动切换至 GPU 集群重新抠图。")
    _save(run)
    return True


def _prepare_full_pipeline_retry(run: dict[str, Any], error: Exception) -> bool:
    """Persist one bounded retry decision before re-entering matting."""

    if str(run.get("status") or "").upper() in {"CANCELED", "WAITING_CHARACTER"}:
        return False
    if str(run.get("stage") or "").lower() in {
        "character_confirmation_timeout",
        "waiting_character",
        "canceled",
    }:
        return False
    recovery = run.setdefault("full_pipeline_recovery", {})
    attempt = int(recovery.get("attempt_count") or run.get("full_pipeline_retry_count") or 0) + 1
    if attempt > MAX_FULL_PIPELINE_RETRIES:
        recovery["exhausted"] = True
        recovery["last_error"] = str(error)
        return False

    entry: dict[str, Any] = {
        "attempt": attempt,
        "triggered_at": _now(),
        "trigger_stage": str(run.get("stage") or ""),
        "error": str(error),
        "source_recovery": [],
    }
    recovery["attempt_count"] = attempt
    recovery["max_attempts"] = MAX_FULL_PIPELINE_RETRIES
    recovery.setdefault("attempts", []).append(entry)
    run["full_pipeline_retry_count"] = attempt
    try:
        entry["source_recovery"] = _ensure_original_images(run)
        _reset_outputs_for_full_retry(run)
    except Exception as recovery_error:
        entry["source_recovery_error"] = str(recovery_error)
        recovery["last_error"] = str(recovery_error)
        run["status"] = "QUEUED"
        run["stage"] = f"full_pipeline_retry_{attempt}_source_recovery"
        run["error"] = str(recovery_error)
        _append_log(
            run,
            f"第 {attempt}/{MAX_FULL_PIPELINE_RETRIES} 次自动恢复暂未找到可靠原图：{recovery_error}",
        )
        _save(run)
        return attempt < MAX_FULL_PIPELINE_RETRIES

    previous_children = run.get("children") if isinstance(run.get("children"), dict) else {}
    entry["previous_children"] = {
        "comfyui_run_id": str(previous_children.get("comfyui_run_id") or ""),
        "cherry_run_ids": list(previous_children.get("cherry_run_ids") or []),
    }
    run["children"] = {}
    run["status"] = "QUEUED"
    run["stage"] = f"full_pipeline_retry_{attempt}_queued"
    run["error"] = ""
    run["worker_pid"] = os.getpid()
    for item in run.get("images") or []:
        for key in (
            "matte_result_path",
            "postprocessed_result_path",
            "comparison_path",
            "result_path",
            "cherry_sequence_group",
            "color_correction_run_id",
        ):
            item[key] = ""
        item["color_correction_status"] = "READY" if item.get("color_reference_path") else ""
        item["position_alignment_status"] = "READY" if item.get("position_alignment_enabled") else ""
        item["reference_postprocess_status"] = "READY" if item.get("color_reference_path") else ""
    _append_log(run, f"自动恢复第 {attempt}/{MAX_FULL_PIPELINE_RETRIES} 次：已恢复原图并从抠图开始完整重跑。")
    _save(run)
    return True


def _ensure_original_images(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Restore missing run originals from source, import storage, or Feishu cache."""

    actions: list[dict[str, Any]] = []
    for item in run.get("images") or []:
        target = Path(str(item.get("original_path") or ""))
        if _is_usable_source_image(target, item):
            continue
        recovered = _recover_original_image(run, item, target)
        if recovered is None:
            raise FileNotFoundError(
                f"original image is missing and no verified persistent source was found: {target}"
            )
        actions.append(recovered)
    if actions:
        run.setdefault("source_recovery_audit", []).extend(actions)
        _append_log(run, f"已从持久化 source/import/飞书缓存恢复 {len(actions)} 张原图。")
        _save(run)
    return actions


def _recover_original_image(
    run: dict[str, Any],
    item: dict[str, Any],
    target: Path,
) -> dict[str, Any] | None:
    exact_candidates: list[Path] = []
    source_path = Path(str(item.get("source_path") or ""))
    if str(source_path):
        exact_candidates.append(source_path)
    for value in item.get("source_evidence") or []:
        candidate = Path(str(value or ""))
        if candidate.suffix.lower() in IMAGE_EXTS:
            exact_candidates.append(candidate)
    for candidate in _deduplicate_paths(exact_candidates):
        if not _is_usable_source_image(candidate, item):
            continue
        _copy_recovered_image(candidate, target, item)
        return _source_recovery_record(item, target, "source_path", candidate)

    names = {
        Path(str(item.get("source_name") or "")).name.casefold(),
        source_path.name.casefold(),
    }
    names.discard("")
    storage_root = Path(settings.storage_dir)
    search_roots = (storage_root / "direct_image_imports", storage_root / "feishu_inbox")
    searched = 0
    for root in search_roots:
        if not root.is_dir():
            continue
        for name in sorted(names):
            for candidate in root.rglob(name):
                if searched >= MAX_SOURCE_RECOVERY_CANDIDATES:
                    break
                if not candidate.is_file():
                    continue
                searched += 1
                if not _is_usable_source_image(candidate, item):
                    continue
                _copy_recovered_image(candidate, target, item)
                return _source_recovery_record(item, target, "persistent_image_cache", candidate)

    for archive in _source_archive_candidates(run, item):
        member = _restore_from_zip_archive(archive, item, target)
        if member:
            return _source_recovery_record(
                item,
                target,
                "feishu_zip_cache",
                archive,
                archive_member=member,
            )
    return None


def _source_archive_candidates(run: dict[str, Any], item: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for value in item.get("source_evidence") or []:
        path = Path(str(value or ""))
        if path.suffix.lower() == ".zip" and path.is_file():
            candidates.append(path)
    label = Path(str(run.get("run_label") or "")).name
    if label.lower().endswith(".zip"):
        inbox = Path(settings.storage_dir) / "feishu_inbox"
        if inbox.is_dir():
            candidates.extend(path for path in inbox.rglob(label) if path.is_file())
    return _deduplicate_paths(candidates)[:MAX_SOURCE_RECOVERY_CANDIDATES]


def _restore_from_zip_archive(
    archive_path: Path,
    item: dict[str, Any],
    target: Path,
) -> str:
    wanted = {
        Path(str(item.get("source_name") or "")).name.casefold(),
        Path(str(item.get("source_path") or "")).name.casefold(),
    }
    wanted.discard("")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and Path(info.filename).name.casefold() in wanted
                and Path(info.filename).suffix.lower() in IMAGE_EXTS
                and 0 <= int(info.file_size) <= MAX_SOURCE_RECOVERY_ZIP_MEMBER_BYTES
            ]
            if len(members) != 1:
                return ""
            member = members[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.recover.tmp")
            try:
                with archive.open(member) as source, temporary.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                if not _is_usable_source_image(temporary, item):
                    return ""
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return member.filename
    except (OSError, ValueError, zipfile.BadZipFile):
        return ""


def _is_usable_source_image(path: Path, item: dict[str, Any]) -> bool:
    if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
        # Recovery temp files intentionally end in .tmp; Pillow still validates them.
        if not path.is_file() or ".recover.tmp" not in path.name:
            return False
    expected_width = int(item.get("width") or 0)
    expected_height = int(item.get("height") or 0)
    expected_size = int(item.get("source_size_bytes") or 0)
    try:
        if expected_size and path.stat().st_size != expected_size:
            return False
        with Image.open(path) as image:
            actual = (int(image.width), int(image.height))
            image.verify()
    except (OSError, ValueError):
        return False
    return not (expected_width and expected_height) or actual == (expected_width, expected_height)


def _copy_recovered_image(source: Path, target: Path, item: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.recover.tmp")
    try:
        shutil.copy2(source, temporary)
        if not _is_usable_source_image(temporary, item):
            raise RuntimeError(f"recovered source image failed validation: {source}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _source_recovery_record(
    item: dict[str, Any],
    target: Path,
    method: str,
    source: Path,
    *,
    archive_member: str = "",
) -> dict[str, Any]:
    return {
        "ts": _now(),
        "item_id": str(item.get("item_id") or item.get("index") or ""),
        "method": method,
        "source": str(source),
        "archive_member": archive_member,
        "restored_path": str(target),
        "sha256": _sha256_file(target),
    }


def _reset_outputs_for_full_retry(run: dict[str, Any]) -> None:
    run_dir = _run_dir(run).resolve()
    for name in ("matte", "smooth", "comparison", "cherry_sequences"):
        target = (run_dir / name).resolve()
        if target.parent != run_dir:
            raise RuntimeError(f"refusing to reset output outside run directory: {target}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)


def _merge_runtime_delivery_state(
    persisted: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    if not persisted:
        return runtime
    for key in ("drive_file", "sequence_zip_path", "sent_files", "delivery", "delivery_artifacts"):
        if runtime.get(key):
            persisted[key] = runtime[key]
    return persisted


def _finish_after_confirmed_delivery(run: dict[str, Any]) -> bool:
    if str(run.get("stage") or "").lower() not in {"send", "done"}:
        return False
    package = Path(str(run.get("sequence_zip_path") or ""))
    artifacts = [item for item in run.get("delivery_artifacts") or [] if isinstance(item, dict)]
    if artifacts:
        confirmed = all(
            str(item.get("status") or "").upper() == "DELIVERED"
            and bool(str(item.get("message_id") or ""))
            and Path(str(item.get("path") or "")).is_file()
            for item in artifacts
        )
        if not package.is_file() or not confirmed:
            return False
        run["sent_files"] = [str(item["path"]) for item in artifacts]
    else:
        receipt = run.get("drive_file") if isinstance(run.get("drive_file"), dict) else {}
        if not package.is_file() or not any(receipt.get(key) for key in ("message_id", "file_token", "url")):
            return False
        run["sent_files"] = [str(package)]
    run["status"] = "DONE"
    run["stage"] = "done"
    run["error"] = ""
    _append_log(run, "发送回执已确认；状态写入恢复后直接收敛为完成，未重复发送文件。")
    _save(run)
    return True


def _should_auto_retry_failed_run(run: dict[str, Any]) -> bool:
    if str(run.get("status") or "").upper() != "FAILED":
        return False
    if str(run.get("stage") or "").lower() in {
        "character_confirmation_timeout",
        "waiting_character",
        "canceled",
        "recovery_exhausted",
    }:
        return False
    attempts = int(
        (run.get("full_pipeline_recovery") or {}).get("attempt_count")
        or run.get("full_pipeline_retry_count")
        or 0
    )
    if attempts >= MAX_FULL_PIPELINE_RETRIES:
        return False
    error = str(run.get("error") or "").casefold()
    recoverable_markers = (
        "winerror 5",
        "permission denied",
        "access is denied",
        "拒绝访问",
        "original image is missing",
        "filenotfounderror",
        "no such file",
    )
    return any(marker in error for marker in recoverable_markers)


def _deduplicate_paths(values: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        key = os.path.normcase(os.path.abspath(str(value)))
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_comfyui(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.comfyui_skills import run_start, run_status

    run_dir = _run_dir(run)
    repair_attempt = 0
    while True:
        matting_generation = int(run.get("matting_generation") or 0) + 1
        run["matting_generation"] = matting_generation
        _save(run)
        result = run_start(
            workflow_path=run.get("workflow_path") or None,
            input_dir=str(run_dir / "original_images"),
            output_dir=str(run_dir / "matte"),
            recursive=True,
            preserve_structure=True,
            skip_existing=repair_attempt > 0,
            notify_interval_seconds=run["notify_interval_seconds"],
            external_batch_id=f"assetclaw:{run['id']}:matting:g{matting_generation}",
            backend="gpu_control" if repair_attempt > 0 else (str(run.get("matting_backend") or "") or None),
        )
        child_id = result["run_id"]
        children = run.setdefault("children", {})
        children["comfyui_run_id"] = child_id
        children.setdefault("comfyui_run_ids", []).append(child_id)
        _append_log(run, f"{'GPU 失败图片补算' if repair_attempt else 'ComfyUI 抠图任务'}已启动：{child_id}")
        _save(run)
        while True:
            if _is_canceled(run):
                return
            payload = run_status(child_id, include_gpu=False)
            children["comfyui"] = payload
            children.setdefault("comfyui_runs", {})[child_id] = payload
            _save(run)
            child_status = str(payload.get("status") or "").upper()
            if child_status not in {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
                time.sleep(5)
                continue
            if child_status == "DONE":
                return
            partial = child_status == "DONE_WITH_ERRORS" and int(payload.get("completed") or 0) > 0
            if partial and repair_attempt < MAX_PARTIAL_MATTING_REPAIRS:
                repair_attempt += 1
                audit = {
                    "attempt": repair_attempt,
                    "source_child_run_id": child_id,
                    "completed_preserved": int(payload.get("completed") or 0),
                    "failed": int(payload.get("failed") or 0),
                    "triggered_at": _now(),
                    "error_items": list(payload.get("error_items") or []),
                }
                run.setdefault("partial_matting_repairs", []).append(audit)
                _append_log(
                    run,
                    f"GPU 批次部分成功：已保留 {audit['completed_preserved']} 张；"
                    f"仅补算 {audit['failed']} 张失败图片（第 {repair_attempt}/{MAX_PARTIAL_MATTING_REPAIRS} 次）。",
                )
                _save(run)
                break
            raise RuntimeError(_format_child_failure("ComfyUI", child_id, payload))


def _run_cherry(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.cherry_skills import run_start, run_status

    run.setdefault("children", {})["cherry_run_ids"] = []
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in run["images"]:
        unit_id = str(item.get("character_unit_id") or item.get("item_id") or f"item:{item.get('index')}")
        profile = str(item.get("cherry_profile") or "").strip().lower()
        if profile not in {"full", "half"}:
            raise RuntimeError(f"Cherry output profile is missing or invalid for image item: {unit_id}")
        reference_path = str(item.get("color_reference_path") or "")
        reference_sha256 = str(item.get("color_reference_sha256") or "").lower()
        if not reference_path or not reference_sha256:
            raise RuntimeError(f"character reference binding is incomplete for image item: {unit_id}")
        grouped.setdefault((unit_id, profile, reference_path, reference_sha256), []).append(item)

    for group_index, ((_unit_id, profile, reference_path, reference_sha256), group_items) in enumerate(grouped.items(), start=1):
        if len(group_items) > 1:
            _run_cherry_sequence_group(
                run,
                group_items,
                group_index=group_index,
                profile=profile,
                reference_path=reference_path,
                reference_sha256=reference_sha256,
                run_start=run_start,
                run_status=run_status,
            )
            continue
        item = group_items[0]
        matte_dir = Path(str(item["matte_dir"]))
        smooth_dir = Path(str(item["smooth_dir"]))
        if not _wait_for_images(matte_dir):
            raise RuntimeError(f"matte_dir has no images: {matte_dir}")
        result = run_start(
            input_dir=str(matte_dir),
            output_dir=str(smooth_dir),
            recursive=True,
            skip_existing=False,
            notify_interval_seconds=run["notify_interval_seconds"],
            profile=profile,
            expected_profile=profile,
            reference_path=reference_path,
            reference_sha256=reference_sha256,
            color_match_required=True,
            alignment_enabled=True,
        )
        options = result.get("options") if isinstance(result.get("options"), dict) else {}
        if options.get("resize_width") and options.get("resize_height"):
            item["cherry_output_size"] = f"{options.get('resize_width')}x{options.get('resize_height')}"
        child_id = result["run_id"]
        run["children"]["cherry_run_id"] = child_id
        run["children"].setdefault("cherry_run_ids", []).append(child_id)
        _append_log(run, f"Cherry 后处理任务已启动：{child_id}，image={item['index']}，profile={item.get('cherry_profile')}")
        _save(run)
        if not _wait_cherry_child(run, child_id, run_status):
            return
        _mark_reference_postprocess_done(item, child_id)
        _save(run)


def _run_cherry_sequence_group(
    run: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    group_index: int,
    profile: str,
    reference_path: str,
    reference_sha256: str,
    run_start: Any,
    run_status: Any,
) -> None:
    """Process one logical sequence together so alignment is shared by every frame."""

    stage_root = _run_dir(run) / "cherry_sequences" / f"group_{group_index:03d}_{uuid.uuid4().hex[:8]}"
    input_dir = stage_root / "input"
    output_dir = stage_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    mappings: list[tuple[dict[str, Any], Path, Path]] = []
    for position, item in enumerate(sorted(items, key=lambda value: int(value.get("index") or 0)), start=1):
        matte_dir = Path(str(item["matte_dir"]))
        if not _wait_for_images(matte_dir):
            raise RuntimeError(f"matte_dir has no images: {matte_dir}")
        matte = _latest_image(matte_dir)
        if not matte:
            raise RuntimeError(f"matte_dir has no result image: {matte_dir}")
        staged = input_dir / f"{position:06d}{matte.suffix.lower()}"
        shutil.copy2(matte, staged)
        mappings.append((item, matte, staged))

    result = run_start(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        recursive=False,
        skip_existing=False,
        notify_interval_seconds=run["notify_interval_seconds"],
        profile=profile,
        expected_profile=profile,
        reference_path=reference_path,
        reference_sha256=reference_sha256,
        color_match_required=True,
        alignment_enabled=True,
    )
    options = result.get("options") if isinstance(result.get("options"), dict) else {}
    child_id = result["run_id"]
    run["children"]["cherry_run_id"] = child_id
    run["children"].setdefault("cherry_run_ids", []).append(child_id)
    _append_log(run, f"Cherry 序列后处理任务已启动：{child_id}，frames={len(items)}，profile={profile}")
    _save(run)
    if not _wait_cherry_child(run, child_id, run_status):
        return

    for item, matte, staged in mappings:
        processed = output_dir / staged.with_suffix(".png").name
        if not processed.is_file():
            raise FileNotFoundError(f"Cherry sequence output missing: {processed}")
        smooth_dir = Path(str(item["smooth_dir"]))
        smooth_dir.mkdir(parents=True, exist_ok=True)
        target = smooth_dir / matte.with_suffix(".png").name
        shutil.copy2(processed, target)
        if options.get("resize_width") and options.get("resize_height"):
            item["cherry_output_size"] = f"{options.get('resize_width')}x{options.get('resize_height')}"
        item["cherry_sequence_group"] = child_id
        _mark_reference_postprocess_done(item, child_id)
    _save(run)


def _wait_cherry_child(run: dict[str, Any], child_id: str, run_status: Any) -> bool:
    while True:
        if _is_canceled(run):
            return False
        payload = run_status(child_id, include_gpu=False)
        run["children"]["cherry"] = payload
        run["children"].setdefault("cherry_runs", {})[child_id] = payload
        _save(run)
        if payload.get("status") in {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
            if payload.get("status") != "DONE":
                raise RuntimeError(_format_child_failure("Cherry", child_id, payload))
            return True
        time.sleep(5)


def _mark_reference_postprocess_done(item: dict[str, Any], child_id: str) -> None:
    item["color_correction_status"] = "DONE"
    item["position_alignment_status"] = "DONE"
    item["reference_postprocess_status"] = "DONE"
    item["color_correction_run_id"] = child_id


def _prepare_character_gate(run: dict[str, Any]) -> bool:
    from assetclaw_matting.services.character_resolution import bind_run_items

    result = bind_run_items("direct_image", str(run["id"]), list(run.get("images") or []))
    if result.get("ready"):
        run.setdefault("character_resolution", {})["pending"] = 0
        run["character_question"] = ""
        _append_log(run, "角色参考图已逐项冻结；Cherry 将在流程末尾依次执行校色和位置矫正。")
        _save(run)
        return True
    unavailable = list(result.get("missing_profiles") or []) + list(result.get("missing") or [])
    if unavailable:
        _append_log(
            run,
            "角色库无对应校色/矫正资料；按交付规则跳过后处理并直接返回透明抠图结果："
            + "、".join(dict.fromkeys(unavailable)),
        )
        _save(run)
        outcome = deliver_matte_only(str(run["id"]), resend=True, _allow_active=True)
        if not outcome.get("ok"):
            raise RuntimeError(str(outcome.get("error") or "透明抠图结果直接交付失败"))
        return False
    run["status"] = "WAITING_CHARACTER"
    run["stage"] = "waiting_character"
    run["recovery_from_stage"] = "postprocess"
    run.setdefault("character_resolution", {})["pending"] = len(result.get("missing") or [])
    run["updated_at"] = _now()
    _append_log(run, "抠图已完成，等待用户确认角色后继续 Cherry 校色/矫正：" + "、".join(result.get("missing") or []))
    _save(run)
    from assetclaw_matting.services.character_resolution import mark_run_waiting

    mark_run_waiting("direct_image", str(run["id"]))
    return False


def deliver_matte_only(
    run_id: str,
    resend: bool = True,
    _allow_active: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Deliver verified matte output and explicitly record skipped postprocessing."""

    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct image run not found"}
    if not _allow_active and str(run.get("status") or "").upper() in {"RUNNING", "QUEUED", "PENDING"}:
        return {"ok": False, "run_id": run_id, "error": "task is still running; matte-only delivery refused"}
    try:
        items = list(run.get("images") or [])
        matte_files: list[tuple[dict[str, Any], Path]] = []
        for item in items:
            matte = _latest_image(Path(str(item.get("matte_dir") or "")))
            if not matte or not matte.is_file():
                raise RuntimeError(f"matte result is missing for {item.get('source_name') or item.get('name')}")
            with Image.open(matte) as image:
                if image.format != "PNG" or "A" not in image.getbands():
                    raise RuntimeError(f"matte result is not a transparent PNG: {matte}")
            item["matte_result_path"] = str(matte)
            item["result_path"] = str(matte)
            item["postprocessed_result_path"] = ""
            item["comparison_path"] = ""
            item["color_correction_status"] = "SKIPPED"
            item["position_alignment_status"] = "SKIPPED"
            item["reference_postprocess_status"] = "SKIPPED"
            matte_files.append((item, matte))

        run["status"] = "RUNNING"
        run["stage"] = "matte_only_delivery"
        run["result_mode"] = "matte_only"
        run["postprocess_skipped"] = {
            "value": True,
            "reason": "character reference unavailable",
            "recorded_at": _now(),
        }
        run["character_question"] = ""
        run.setdefault("character_resolution", {})["pending"] = 0
        run["error"] = ""
        _save(run)

        receipts: list[dict[str, str]] = []
        sent_files: list[str] = []
        if resend and run.get("chat_id"):
            from assetclaw_matting.feishu.client import feishu_client

            chat_id = str(run["chat_id"])
            if bool(run.get("package_as_sequence")) or len(matte_files) > 1:
                package = _make_matte_only_zip(run, matte_files)
                receipt = feishu_client.send_file_to_chat(chat_id, package, package.name) or {}
                receipts.append(receipt)
                sent_files.append(str(package))
                run["sequence_zip_path"] = str(package)
                run["delivery_artifacts"] = [{
                    "kind": "complete_bundle",
                    "label": "序列帧完整结果包（原始帧、透明抠图、后处理说明）",
                    "path": str(package),
                    "file_name": package.name,
                    "size_bytes": package.stat().st_size,
                    "sha256": _sha256_file(package),
                    "status": "DELIVERED" if receipt.get("message_id") else "FAILED",
                    "message_id": str(receipt.get("message_id") or ""),
                    "file_token": str(receipt.get("file_token") or ""),
                    "url": str(receipt.get("url") or ""),
                    "delivery_method": str(receipt.get("delivery_method") or ""),
                }]
            else:
                item, matte = matte_files[0]
                base = Path(str(item.get("source_name") or item.get("name") or matte.name)).stem
                receipt = feishu_client.send_file_to_chat(chat_id, matte, f"{base}_matte.png") or {}
                receipts.append(receipt)
                sent_files.append(str(matte))
            if not all(str(receipt.get("message_id") or "") for receipt in receipts):
                raise RuntimeError("Feishu matte-only delivery returned no message receipt")
            run["delivery"] = {
                "status": "DELIVERED",
                "chat_id": chat_id,
                "artifact_count": len(sent_files),
                "delivered_count": len(sent_files),
                "delivered_at": _now(),
                "postprocess_applied": False,
            }
            _notify(
                run,
                f"抠图结果已交付：{run['id']}。\n"
                "序列帧已返回 1 个完整 ZIP，包内包含原始帧、透明抠图和后处理说明。\n"
                "未生成后处理结果：角色库中暂无对应角色的校色与位置矫正资料。",
            )

        from assetclaw_matting.services.character_resolution import cancel_run_resolutions

        cancel_run_resolutions("direct_image", str(run["id"]))
        run["matte_only_delivery_receipts"] = receipts
        run["sent_files"] = sent_files
        run["status"] = "DONE"
        run["stage"] = "done_matte_only"
        run["error"] = ""
        run["updated_at"] = _now()
        _append_log(run, "透明抠图结果已交付；角色校色与位置矫正未执行。")
        _save(run)
        return {"ok": True, "run_id": run_id, **_public(run)}
    except Exception as exc:
        run = _load(run_id) or run
        run["status"] = "FAILED"
        run["stage"] = "matte_only_delivery_failed"
        run["error"] = str(exc)
        run["updated_at"] = _now()
        _append_log(run, f"透明抠图结果直接交付失败：{exc}")
        _save(run)
        return {"ok": False, "run_id": run_id, "error": str(exc), **_public(run)}


def _make_matte_only_zip(
    run: dict[str, Any],
    matte_files: list[tuple[dict[str, Any], Path]],
) -> Path:
    name = _safe_name(Path(str(run.get("run_label") or run["id"])).stem) or str(run["id"])
    package = _run_dir(run) / f"{name}_matte_only.zip"
    package.parent.mkdir(parents=True, exist_ok=True)
    partial = package.with_suffix(".zip.part")
    manifest = {
        "schema_version": "2.0",
        "run_id": run.get("id"),
        "artifact_kind": "sequence_complete_bundle",
        "frame_count": len(matte_files),
        "ordered": True,
        "postprocess_applied": False,
        "files": [],
    }
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            for position, (item, matte) in enumerate(matte_files):
                original = Path(str(item.get("original_path") or ""))
                if not original.is_file() or not matte.is_file():
                    raise RuntimeError(f"matte-only sequence source is missing: original={original}, matte={matte}")
                original_entry = f"01_original_frames/{position:04d}{original.suffix.lower() or '.png'}"
                matte_entry = f"02_matte/{position:04d}{matte.suffix.lower() or '.png'}"
                bundle.write(original, original_entry, compress_type=zipfile.ZIP_STORED)
                bundle.write(matte, matte_entry, compress_type=zipfile.ZIP_STORED)
                manifest["files"].append({
                    "index": position,
                    "source_name": item.get("source_name") or item.get("name") or "",
                    "original_frames": original_entry,
                    "matte": matte_entry,
                })
            bundle.writestr(
                "03_postprocessed/README.txt",
                "未生成后处理结果：角色库中暂无对应角色的校色与位置矫正资料。\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
            bundle.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
                compress_type=zipfile.ZIP_DEFLATED,
            )
        with zipfile.ZipFile(partial, "r") as bundle:
            if bundle.testzip() is not None or len(bundle.infolist()) != len(matte_files) * 2 + 2:
                raise RuntimeError(f"matte-only ZIP verification failed: {partial}")
        os.replace(partial, package)
    finally:
        partial.unlink(missing_ok=True)
    return package


def _send_results(run: dict[str, Any]) -> list[str]:
    chat_id = str(run.get("chat_id") or "")
    if not chat_id:
        return []
    from assetclaw_matting.feishu.client import feishu_client

    items = list(run.get("images") or [])
    prepared = _prepare_result_files(run)
    if bool(run.get("package_as_sequence")) or len(items) > 1:
        zip_path = _make_sequence_zip(run)
        run["sequence_zip_path"] = str(zip_path)
        sent = _send_sequence_delivery_artifacts(run, chat_id=chat_id)
        _append_log(run, f"序列帧完整结果包已发送：原始帧、透明抠图、后处理结果，共 {len(items)} 帧")
        _save(run)
        return sent

    sent: list[str] = []
    for item, (matte, processed, comparison) in zip(items, prepared):
        base_name = Path(str(item.get("source_name") or item.get("name") or processed.name)).stem
        matte_send_name = f"{base_name}_matte{matte.suffix.lower()}"
        processed_send_name = f"{base_name}_processed{processed.suffix.lower()}"
        feishu_client.send_file_to_chat(chat_id, matte, matte_send_name)
        feishu_client.send_file_to_chat(chat_id, processed, processed_send_name)
        feishu_client.send_image_to_chat(chat_id, comparison)
        sent.extend([str(matte), str(processed), str(comparison)])
        _append_log(run, f"已发送三份图片结果：{matte.name}、{processed.name}、{comparison.name}")
        _save(run)
    return sent


def _prepare_result_files(run: dict[str, Any]) -> list[tuple[Path, Path, Path]]:
    prepared: list[tuple[Path, Path, Path]] = []
    for item in run.get("images") or []:
        matte_dir = Path(str(item["matte_dir"]))
        smooth_dir = Path(str(item["smooth_dir"]))
        original = Path(str(item.get("original_path") or ""))
        matte = _latest_image(matte_dir)
        processed = _latest_image(smooth_dir)
        if not original.is_file():
            raise RuntimeError(f"original image is missing: {original}")
        if not matte:
            raise RuntimeError(f"matte_dir has no result image: {matte_dir}")
        if not processed:
            raise RuntimeError(f"smooth_dir has no result image: {smooth_dir}")

        base_name = Path(str(item.get("source_name") or item.get("name") or processed.name)).stem
        comparison = _run_dir(run) / "comparison" / f"{base_name}_comparison.png"
        if not comparison.is_file():
            _create_comparison_image(original, matte, processed, comparison)

        item["matte_result_path"] = str(matte)
        item["postprocessed_result_path"] = str(processed)
        item["comparison_path"] = str(comparison)
        item["result_path"] = str(processed)
        prepared.append((matte, processed, comparison))
    _save(run)
    return prepared


def package_and_send(run_id: str | None = None, package_name: str = "", **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    if not run.get("chat_id"):
        return {"ok": False, "error": "run has no Feishu chat_id"}
    _prepare_result_files(run)
    zip_path = _make_sequence_zip(run, package_name=package_name)
    run["sequence_zip_path"] = str(zip_path)
    sent_files = _send_sequence_delivery_artifacts(run, chat_id=str(run["chat_id"]))
    run["sent_files"] = sent_files
    run["status"] = "DONE"
    run["stage"] = "done"
    run["error"] = ""
    _append_log(run, f"序列帧完整结果包已补发：原始帧、透明抠图、后处理结果，共 {len(run.get('images') or [])} 帧")
    _save(run)
    return {
        "ok": True,
        "run_id": run["id"],
        "zip_path": str(zip_path),
        "zip_name": zip_path.name,
        "frame_count": len(run.get("images") or []),
        "sent_files": sent_files,
        "delivery_artifacts": run.get("delivery_artifacts") or [],
    }


def _make_sequence_zip(run: dict[str, Any], package_name: str = "") -> Path:
    items = sorted(run.get("images") or [], key=lambda item: int(item.get("index") or 0))
    if not items:
        raise RuntimeError("sequence run contains no images")
    if package_name:
        zip_name = _safe_name(package_name)
        if not zip_name.lower().endswith(".zip"):
            zip_name += ".zip"
    else:
        label = str(run.get("run_label") or "").strip()
        if not label or "、" in label or label.lower().startswith("feishu_image"):
            label = f"序列帧_{len(items)}张"
        zip_name = f"{Path(_safe_name(label)).stem}_animation_processed.zip"
    zip_path = _run_dir(run) / zip_name
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "2.0",
        "run_id": run.get("id"),
        "artifact_kind": "sequence_complete_bundle",
        "frame_count": len(items),
        "ordered": True,
        "stages": ["original_frames", "matte", "postprocessed"],
        "files": [],
    }
    partial = zip_path.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for position, item in enumerate(items):
                paths = {
                    "original_frames": Path(str(item.get("original_path") or "")),
                    "matte": Path(str(item.get("matte_result_path") or "")),
                    "postprocessed": Path(str(item.get("postprocessed_result_path") or "")),
                }
                missing = [f"{kind}:{path}" for kind, path in paths.items() if not path.is_file()]
                if missing:
                    raise RuntimeError("sequence package missing result files: " + ", ".join(missing))
                entries: dict[str, str] = {}
                for order, (kind, path) in enumerate(paths.items(), start=1):
                    entry = f"{order:02d}_{kind}/{position:04d}{path.suffix.lower() or '.png'}"
                    archive.write(path, entry, compress_type=zipfile.ZIP_STORED)
                    entries[kind] = entry
                manifest["files"].append({
                    "index": position,
                    "source_name": item.get("source_name") or item.get("name") or "",
                    **entries,
                })
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
                compress_type=zipfile.ZIP_DEFLATED,
            )
        with zipfile.ZipFile(partial, "r") as archive:
            if archive.testzip() is not None or len(archive.infolist()) != len(items) * 3 + 1:
                raise RuntimeError(f"sequence complete ZIP verification failed: {partial}")
        os.replace(partial, zip_path)
    finally:
        partial.unlink(missing_ok=True)
    return zip_path


def _send_sequence_delivery_artifacts(
    run: dict[str, Any],
    *,
    chat_id: str,
    attempts: int = 5,
) -> list[str]:
    from assetclaw_matting.feishu.client import feishu_client

    package = Path(str(run.get("sequence_zip_path") or ""))
    if not package.is_file():
        raise RuntimeError(f"sequence complete bundle is missing: {package}")
    specs = [{
        "kind": "complete_bundle",
        "label": "序列帧完整结果包（原始帧、透明抠图、后处理结果）",
        "path": str(package),
    }]
    previous = {
        str(item.get("kind") or ""): item
        for item in run.get("delivery_artifacts") or []
        if isinstance(item, dict)
    }
    artifacts: list[dict[str, Any]] = []
    for spec in specs:
        path = Path(spec["path"])
        prior = previous.get(spec["kind"], {})
        sha256 = _sha256_file(path)
        same_file = (
            str(prior.get("path") or "") == str(path)
            and int(prior.get("size_bytes") or 0) == path.stat().st_size
            and str(prior.get("sha256") or "") == sha256
        )
        artifacts.append({
            **spec,
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256,
            "status": str(prior.get("status") or "PENDING") if same_file else "PENDING",
            "message_id": str(prior.get("message_id") or "") if same_file else "",
            "file_token": str(prior.get("file_token") or "") if same_file else "",
            "url": str(prior.get("url") or "") if same_file else "",
            "delivery_method": str(prior.get("delivery_method") or "") if same_file else "",
            "attempts": int(prior.get("attempts") or 0) if same_file else 0,
            "last_error": str(prior.get("last_error") or "") if same_file else "",
        })
    run["delivery_artifacts"] = artifacts
    _save(run)
    for artifact in artifacts:
        if artifact.get("status") == "DELIVERED" and artifact.get("message_id"):
            continue
        errors: list[str] = []
        for attempt in range(1, max(1, attempts) + 1):
            artifact.update(status="UPLOADING", attempts=attempt, last_error="")
            _save(run)
            try:
                path = Path(str(artifact["path"]))
                receipt = feishu_client.send_file_to_chat(chat_id, path, str(artifact["file_name"])) or {}
                message_id = str(receipt.get("message_id") or "")
                if not message_id:
                    raise RuntimeError(f"Feishu delivery returned no message receipt for {path.name}")
                artifact.update({
                    "status": "DELIVERED",
                    "message_id": message_id,
                    "file_token": str(receipt.get("file_token") or ""),
                    "url": str(receipt.get("url") or ""),
                    "delivery_method": str(receipt.get("delivery_method") or ""),
                    "delivered_at": _now(),
                    "last_error": "",
                })
                if receipt.get("file_token") or receipt.get("url"):
                    run["drive_file"] = receipt
                _append_log(run, f"已交付{artifact['label']}：{path.name}，message_id={message_id}")
                _save(run)
                break
            except Exception as exc:
                errors.append(str(exc))
                artifact["status"] = "RETRYING" if attempt < attempts else "FAILED"
                artifact["last_error"] = str(exc)
                _append_log(run, f"{artifact['label']}发送第 {attempt}/{attempts} 次失败：{exc}")
                _save(run)
                if attempt < attempts:
                    time.sleep(min(5 * attempt, 15))
        if artifact.get("status") != "DELIVERED":
            raise RuntimeError(
                f"{artifact['label']} delivery failed after {attempts} attempts: "
                f"{errors[-1] if errors else 'unknown error'}"
            )
    sent = [str(item["path"]) for item in artifacts if item.get("status") == "DELIVERED"]
    run["sent_files"] = sent
    run["delivery"] = {
        "status": "DELIVERED",
        "chat_id": chat_id,
        "artifact_count": len(artifacts),
        "delivered_count": len(sent),
        "complete_bundle_path": str(run.get("sequence_zip_path") or ""),
        "postprocess_applied": True,
        "delivered_at": _now(),
    }
    _save(run)
    return sent


def _create_comparison_image(original_path: Path, matte_path: Path, processed_path: Path, output_path: Path) -> Path:
    """Create a readable original/matte/post-process triptych for Feishu preview."""
    labels = ("原图", "抠图结果", "后处理结果")
    paths = (original_path, matte_path, processed_path)
    panel_width = 480
    image_height = 520
    label_height = 64
    outer_margin = 24
    gap = 18
    panel_height = label_height + image_height
    canvas_width = outer_margin * 2 + panel_width * 3 + gap * 2
    canvas_height = outer_margin * 2 + panel_height
    canvas = Image.new("RGB", (canvas_width, canvas_height), (25, 27, 32))
    draw = ImageDraw.Draw(canvas)
    font = _comparison_font(28)

    for index, (label, path) in enumerate(zip(labels, paths)):
        left = outer_margin + index * (panel_width + gap)
        top = outer_margin
        draw.rounded_rectangle(
            (left, top, left + panel_width - 1, top + panel_height - 1),
            radius=12,
            fill=(245, 246, 248),
            outline=(73, 77, 87),
            width=2,
        )
        label_box = draw.textbbox((0, 0), label, font=font)
        label_width = label_box[2] - label_box[0]
        draw.text(
            (left + (panel_width - label_width) // 2, top + 14),
            label,
            font=font,
            fill=(34, 37, 44),
        )

        image_top = top + label_height
        checker = _checkerboard((panel_width - 4, image_height - 4))
        canvas.paste(checker, (left + 2, image_top + 2))
        with Image.open(path) as source:
            rgba = ImageOps.exif_transpose(source).convert("RGBA")
            fitted = ImageOps.contain(rgba, (panel_width - 28, image_height - 28), Image.Resampling.LANCZOS)
        x = left + (panel_width - fitted.width) // 2
        y = image_top + (image_height - fitted.height) // 2
        canvas.paste(fitted, (x, y), fitted)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path


def _checkerboard(size: tuple[int, int], cell: int = 20) -> Image.Image:
    board = Image.new("RGB", size, (235, 235, 235))
    draw = ImageDraw.Draw(board)
    alternate = (207, 207, 207)
    width, height = size
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, min(x + cell - 1, width - 1), min(y + cell - 1, height - 1)), fill=alternate)
    return board


def _comparison_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def resume_from_postprocess(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct image run not found"}
    if not _prepare_character_gate(run):
        return {"ok": True, "run_id": run["id"], **_public(run)}
    run["status"] = "RUNNING"
    run["stage"] = "postprocess"
    run["error"] = ""
    _append_log(run, "从已生成的抠图结果继续 Cherry 后处理。")
    _save(run)
    _run_cherry(run)
    _mark(run, "RUNNING", "send")
    sent = _send_results(run)
    run["sent_files"] = sent
    run["status"] = "DONE"
    run["stage"] = "done"
    run["error"] = ""
    _append_log(run, f"结果文件发送完成：{len(sent)} 个")
    _save(run)
    return {"ok": True, "run_id": run["id"], **_public(run)}


def _format_stage_summary(run: dict[str, Any], title: str) -> str:
    parts = [f"{title}：{run['id']}"]
    details = [f"图片 {len(run.get('images') or [])}"]
    children = run.get("children") or {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    if comfy:
        details.append(f"抠图 {comfy.get('completed', 0)}/{comfy.get('total', 0)}")
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    if cherry:
        details.append(f"后处理 {cherry.get('completed', 0)}/{cherry.get('total', 0)}")
    cherry_plan = _cherry_plan_summary(run.get("images") or [])
    if cherry_plan:
        details.append(cherry_plan)
    if details:
        parts.append("，".join(details))
    return "；".join(parts)


def _format_child_failure(kind: str, run_id: str, payload: dict[str, Any]) -> str:
    detail = [
        f"{kind} run {run_id} ended as {payload.get('status')}",
        f"completed={payload.get('completed', 0)}/{payload.get('total', 0)}",
        f"failed={payload.get('failed', 0)}",
    ]
    if payload.get("last_error"):
        detail.append(f"last_error={payload.get('last_error')}")
    if payload.get("error"):
        detail.append(f"error={payload.get('error')}")
    return "；".join(detail)


def _user_failure_notice(run_id: str, error: Exception | str, *, retries_exhausted: bool = False) -> str:
    detail = str(error or "")
    if "result manifest fields do not match" in detail.lower():
        reason = "抠图计算已经完成，但集群返回结果的协议校验未通过，因此没有交付错误文件。"
    else:
        reason = "任务未能完成，详细技术原因已经记录在后台日志。"
    retry = "系统已停止自动重试，避免重复消耗 GPU。" if retries_exhausted else "系统不会交付不完整结果。"
    return f"图片任务未完成：{run_id}\n{reason}\n{retry}"


def _close_failed_character_resolution(run: dict[str, Any]) -> None:
    from assetclaw_matting.services.character_resolution import fail_run_resolutions

    fail_run_resolutions("direct_image", str(run.get("id") or ""))
    run["character_question"] = ""
    run.setdefault("character_resolution", {})["pending"] = 0


def _cancel_child_runs(run: dict[str, Any]) -> list[dict[str, Any]]:
    children = run.get("children") if isinstance(run.get("children"), dict) else {}
    results: list[dict[str, Any]] = []
    comfy_id = str(children.get("comfyui_run_id") or "").strip()
    if comfy_id:
        try:
            from assetclaw_matting.skills.comfyui_skills import run_cancel as cancel_comfyui

            result = cancel_comfyui(comfy_id, interrupt_current=True, notify=False)
            results.append({"kind": "ComfyUI", "run_id": comfy_id, "ok": bool(result.get("ok")), "status": result.get("status"), "error": result.get("error") or result.get("queue_error") or ""})
        except Exception as exc:
            results.append({"kind": "ComfyUI", "run_id": comfy_id, "ok": False, "error": str(exc)})
    cherry_ids = list(dict.fromkeys(str(item).strip() for item in (children.get("cherry_run_ids") or []) if str(item).strip()))
    cherry_id = str(children.get("cherry_run_id") or "").strip()
    if cherry_id and cherry_id not in cherry_ids:
        cherry_ids.append(cherry_id)
    for child_id in cherry_ids:
        try:
            from assetclaw_matting.skills.cherry_skills import run_cancel as cancel_cherry

            result = cancel_cherry(child_id, notify=False)
            results.append({"kind": "Cherry", "run_id": child_id, "ok": bool(result.get("ok")), "status": result.get("status"), "error": result.get("error") or ""})
        except Exception as exc:
            results.append({"kind": "Cherry", "run_id": child_id, "ok": False, "error": str(exc)})
    return results


def _child_cancel_label(item: dict[str, Any]) -> str:
    status = item.get("status") or ("OK" if item.get("ok") else "失败")
    return f"{item.get('kind')} {item.get('run_id')} {status}"


def _notify(run: dict[str, Any], text: str) -> None:
    chat_id = str(run.get("chat_id") or "")
    if not chat_id:
        return
    from assetclaw_matting.services.notification_service import send_text

    try:
        send_text(chat_id, text)
    except Exception as exc:
        _append_log(run, f"通知发送失败（不影响主任务）：{exc}")
        _save(run)


def _start_worker(run_id: str) -> None:
    if run_id in _WORKERS:
        return
    _WORKERS.add(run_id)
    threading.Thread(
        target=_worker_with_runs_root,
        args=(_worker, run_id, Path(RUNS_ROOT)),
        name=f"direct_image_{run_id}",
        daemon=True,
    ).start()


def _start_recovery_worker(run_id: str) -> bool:
    if run_id in _WORKERS:
        return False
    _WORKERS.add(run_id)
    threading.Thread(
        target=_worker_with_runs_root,
        args=(_resume_worker, run_id, Path(RUNS_ROOT)),
        name=f"direct_image_recovery_{run_id}",
        daemon=True,
    ).start()
    return True


def _worker_with_runs_root(worker: Any, run_id: str, runs_root: Path) -> None:
    """Pin a background worker to the storage root active when scheduled."""
    _RUN_CONTEXT.runs_root = Path(runs_root)
    try:
        worker(run_id)
    finally:
        if hasattr(_RUN_CONTEXT, "runs_root"):
            delattr(_RUN_CONTEXT, "runs_root")


def _resume_worker(run_id: str) -> None:
    run = _load(run_id)
    if not run:
        _WORKERS.discard(run_id)
        return
    run["worker_pid"] = os.getpid()
    _save(run)
    previous_stage = str(run.get("recovery_from_stage") or run.get("stage") or "").lower()
    try:
        if "matting" in previous_stage or "comfy" in previous_stage:
            _mark(run, "RUNNING", "recovery_matting")
            _resume_existing_comfyui_child(run)
            previous_stage = "postprocess"
        elif not any(token in previous_stage for token in ("postprocess", "cherry", "smooth", "send", "delivery")):
            _append_log(run, f"恢复点 {previous_stage or 'unknown'} 尚未创建远端批次，从原始图片继续执行")
            _save(run)
            _worker(run_id)
            return

        if any(token in previous_stage for token in ("postprocess", "cherry", "smooth")):
            if not _prepare_character_gate(run):
                return
            _mark(run, "RUNNING", "recovery_postprocess")
            _run_cherry(run)
        if _is_canceled(run):
            return
        _mark(run, "RUNNING", "recovery_send")
        sent = _send_results(run)
        run["sent_files"] = sent
        run["status"] = "DONE"
        run["stage"] = "done"
        run["error"] = ""
        run["updated_at"] = _now()
        _append_log(run, f"图片任务从持久化断点恢复并发送完成：{len(sent)} 个文件")
        _save(run)
    except Exception as exc:
        latest = _load(run_id) or run
        if latest.get("status") != "CANCELED":
            latest["status"] = "FAILED"
            latest["stage"] = "recovery_failed"
            latest["error"] = str(exc)
            latest["updated_at"] = _now()
            _append_log(latest, f"图片任务持久化恢复失败：{exc}")
            _save(latest)
            _notify(latest, _user_failure_notice(run_id, exc, retries_exhausted=True))
    finally:
        latest = _load(run_id)
        if latest and int(latest.get("worker_pid") or 0) == os.getpid():
            latest["worker_pid"] = 0
            _save(latest)
        _WORKERS.discard(run_id)
        _restart_character_resume_if_needed(run_id)


def _restart_character_resume_if_needed(run_id: str) -> None:
    latest = _load(run_id)
    if not latest or str(latest.get("status") or "") != "QUEUED":
        return
    if str(latest.get("stage") or "") != "character_resolved":
        return
    from assetclaw_matting.services.character_resolution import all_run_units_frozen

    if all_run_units_frozen("direct_image", run_id):
        _start_recovery_worker(run_id)


def _resume_existing_comfyui_child(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.comfyui_skills import run_resume, run_status

    child_id = str((run.get("children") or {}).get("comfyui_run_id") or "")
    if not child_id:
        _run_comfyui(run)
        return
    payload = run_status(child_id, include_gpu=False)
    status_text = str(payload.get("status") or "").upper()
    if status_text not in {"DONE", "RUNNING", "QUEUED", "PENDING", "PAUSED"}:
        _append_log(run, f"原抠图子任务 {child_id} 为 {status_text}，创建新的代次重试")
        _run_comfyui(run)
        return
    if status_text != "DONE":
        run_resume(child_id)
    while status_text != "DONE":
        if _is_canceled(run):
            return
        payload = run_status(child_id, include_gpu=False)
        run.setdefault("children", {})["comfyui"] = payload
        _save(run)
        status_text = str(payload.get("status") or "").upper()
        if status_text in {"FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
            raise RuntimeError(_format_child_failure("ComfyUI", child_id, payload))
        if status_text != "DONE":
            time.sleep(5)
    _append_log(run, f"已重新挂接持久化抠图子任务：{child_id}")
    _save(run)


def _comfyui_child_status(run_id: str) -> dict[str, Any]:
    if not run_id:
        return {}
    try:
        from assetclaw_matting.skills.comfyui_skills import run_status

        return run_status(run_id, include_gpu=False)
    except Exception:
        return {}


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except ImportError:
        pass
    except psutil.AccessDenied:
        # Access can be denied for a healthy process owned by another token.
        return True
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError, ValueError):
        return False


def _reconcile_terminal_child(run: dict[str, Any]) -> dict[str, Any]:
    if str(run.get("status") or "").upper() != "RUNNING" or str(run.get("stage") or "") != "matting":
        return run
    child_id = str((run.get("children") or {}).get("comfyui_run_id") or "")
    if not child_id:
        return run
    try:
        from assetclaw_matting.skills.comfyui_skills import run_status

        payload = run_status(child_id, include_gpu=False)
    except Exception:
        return run
    child_status = str(payload.get("status") or "").upper()
    if child_status not in {"FAILED", "CANCELED"}:
        return run
    run.setdefault("children", {})["comfyui"] = payload
    run["status"] = child_status
    run["stage"] = "failed" if child_status == "FAILED" else "canceled"
    run["error"] = _format_child_failure("ComfyUI", child_id, payload)
    run["updated_at"] = _now()
    _save(run)
    return run


def _is_canceled(run: dict[str, Any]) -> bool:
    latest = _load(run["id"])
    return bool(latest and latest.get("status") == "CANCELED")


def _mark(run: dict[str, Any], status_text: str, stage: str) -> None:
    run["status"] = status_text
    run["stage"] = stage
    run["updated_at"] = _now()
    _append_log(run, f"进入阶段：{stage}")
    _save(run)


def _append_log(run: dict[str, Any], message: str) -> None:
    logs = run.setdefault("log", [])
    logs.append({"ts": _now(), "message": str(message)})
    run["log"] = logs[-120:]
    run["updated_at"] = _now()


def _public(run: dict[str, Any]) -> dict[str, Any]:
    terminal = str(run.get("status") or "").upper() in FINISHED
    character_resolution = dict(run.get("character_resolution") or {})
    if terminal:
        character_resolution["pending"] = 0
    return {
        "status": run.get("status"),
        "stage": run.get("stage"),
        "run_label": run.get("run_label"),
        "package_as_sequence": bool(run.get("package_as_sequence")),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "images": run.get("images") or [],
        "children": run.get("children") or {},
        "sent_files": run.get("sent_files") or [],
        "sequence_zip_path": run.get("sequence_zip_path") or "",
        "drive_file": run.get("drive_file") or {},
        "delivery": run.get("delivery") or {},
        "delivery_artifacts": run.get("delivery_artifacts") or [],
        "result_mode": run.get("result_mode") or "full",
        "postprocess_skipped": run.get("postprocess_skipped") or {},
        "character_question": "" if terminal else (run.get("character_question") or ""),
        "character_resolution": character_resolution,
        "pipeline_notice": run.get("pipeline_notice") or "",
        "error": run.get("error") or "",
        "last_log": (run.get("log") or [{}])[-1].get("message", ""),
        "run_dir": str(_run_dir(run)),
    }


def _brief_pipeline_notice(text: str) -> str:
    if "已自动更新" in (text or ""):
        return "管线已自动更新"
    if "最新" in (text or ""):
        return "管线已确认最新"
    return "管线已确认"


def _cherry_output_size(profile: str) -> str:
    return "256x256" if str(profile or "").lower() in {"half", "emoji", "square"} else "384x512"


def _cherry_profile_from_dimensions(width: int, height: int) -> str:
    """Mirror Cherry's near-square rule, then freeze the resulting output profile."""

    if width > 0 and height > 0 and abs((float(width) / float(height)) - 1.0) <= 0.01:
        return "half"
    return "full"


def _cherry_plan_summary(items: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        profile = str(item.get("cherry_profile") or "")
        if not profile:
            continue
        aspect = "正方形" if str(item.get("aspect") or "").lower() == "square" or profile == "half" else "长方形"
        size = str(item.get("cherry_output_size") or _cherry_output_size(profile))
        key = f"{aspect} {size}"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    return "后处理 " + "，".join(f"{key}×{count}" for key, count in counts.items())


def _character_completion_lines(items: list[dict[str, Any]]) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for item in items:
        unit_id = str(item.get("character_unit_id") or item.get("item_id") or item.get("index") or "")
        if unit_id in seen:
            continue
        seen.add(unit_id)
        name = str(item.get("source_name") or item.get("name") or "图片")
        character = str(item.get("character_id") or "未知角色")
        lines.append(f"{name} · {character} · 后处理（含校色/矫正）完成")
    return ("\n" + "\n".join(lines)) if lines else ""


def _load(run_id: str | None = None) -> dict[str, Any] | None:
    runs_root = _active_runs_root()
    if run_id:
        path = runs_root / run_id / "status.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        matched = _find_run_by_text(run_id)
        if matched:
            return matched
        return None
    if not runs_root.exists():
        return None
    paths = sorted(runs_root.glob("IMG_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not paths:
        return None
    active = []
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if run.get("status") not in FINISHED:
            active.append(run)
    return active[0] if active else json.loads(paths[0].read_text(encoding="utf-8"))


def _find_run_by_text(value: str) -> dict[str, Any] | None:
    query = str(value or "").strip().lower()
    runs_root = _active_runs_root()
    if not query or not runs_root.exists():
        return None
    paths = sorted(runs_root.glob("IMG_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    fallback: dict[str, Any] | None = None
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        haystack = " ".join(
            [str(run.get("id") or ""), str(run.get("run_label") or "")]
            + [str(item.get("name") or item.get("source_path") or item.get("original_path") or "") for item in run.get("images") or []]
        ).lower()
        if query not in haystack:
            continue
        if run.get("status") not in FINISHED:
            return run
        fallback = fallback or run
    return fallback


def _save(run: dict[str, Any], *, expected_statuses: set[str] | None = None) -> bool:
    from assetclaw_matting.services.atomic_json_state import atomic_save_task_json

    path = _run_dir(run) / "status.json"
    return atomic_save_task_json(path, run, expected_statuses=expected_statuses)


def _run_dir(run: dict[str, Any]) -> Path:
    return _active_runs_root() / str(run["id"])


def _active_runs_root() -> Path:
    return Path(getattr(_RUN_CONTEXT, "runs_root", RUNS_ROOT))


def _validate_image(path: str) -> Path:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError(f"image path must be a file: {target}")
    if target.suffix.lower() not in IMAGE_EXTS:
        raise ValueError(f"unsupported image extension: {target.suffix}")
    return target


def _safe_name(value: str) -> str:
    text = str(value or "").replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip(" .")
    return cleaned or "image.png"


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return int(img.width), int(img.height)


def _latest_image(folder: Path) -> Path | None:
    images = [path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS]
    if not images:
        return None
    return max(images, key=lambda path: path.stat().st_mtime)


def _wait_for_images(folder: Path, timeout_seconds: float = 10.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() <= deadline:
        if any(path.is_file() and path.suffix.lower() in IMAGE_EXTS for path in folder.rglob("*")):
            return True
        time.sleep(0.5)
    return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

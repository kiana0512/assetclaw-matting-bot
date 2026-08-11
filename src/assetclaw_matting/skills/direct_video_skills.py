from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from assetclaw_matting.config import settings
from assetclaw_matting.runtime_context import get_runtime_context
from assetclaw_matting.skills import matting_pipeline_skills
from assetclaw_matting.skills.media_skills import VIDEO_EXTS
from assetclaw_matting.skills.sequence_integrity import validate_matte_sequence, validate_sequence_names
from assetclaw_matting.skills.security import validate_path


RUNS_ROOT = Path(settings.storage_dir) / "direct_video_runs"
FINISHED = {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}
_WORKERS: set[str] = set()
_COMFYUI_SEQUENCE_LOCK = threading.Lock()
MAX_PARTIAL_MATTING_REPAIRS = 2


def start(
    video_paths: list[str],
    source_names: list[str] | None = None,
    fps: int = 24,
    max_frames: int = 0,
    workflow_path: str | None = None,
    notify_interval_seconds: int = 60,
    run_label: str = "",
    matting_backend: str | None = None,
    character_evidence: list[list[str]] | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not video_paths:
        raise ValueError("video_paths is required")
    videos = [_validate_video(path) for path in video_paths]
    pipeline_notice = ""
    if not workflow_path and Path(settings.comfyui_workflow_path).name == settings.matting_pipeline_workflow_name:
        pipeline = matting_pipeline_skills.ensure_latest_for_task()
        if not pipeline.get("ok"):
            raise RuntimeError(str(pipeline.get("error") or "matting pipeline preflight failed"))
        workflow_path = str(pipeline.get("workflow_path") or "")
        pipeline_notice = str(pipeline.get("message") or "")
    names = list(source_names or [])
    evidence_sets = list(character_evidence or [])
    run_id = "VID_" + uuid.uuid4().hex[:12].upper()
    run_dir = RUNS_ROOT / run_id
    originals_dir = run_dir / "original_videos"
    frames_dir = run_dir / "frames"
    matte_dir = run_dir / "matte"
    smooth_dir = run_dir / "smooth"
    originals_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    matte_dir.mkdir(parents=True, exist_ok=True)
    smooth_dir.mkdir(parents=True, exist_ok=True)

    copied = []
    for index, video in enumerate(videos, start=1):
        name = _safe_name(names[index - 1] if index - 1 < len(names) else video.name)
        suffix = video.suffix if video.suffix.lower() in VIDEO_EXTS else ".mp4"
        target = originals_dir / f"{index:02d}_{Path(name).stem}{suffix}"
        shutil.copy2(video, target)
        copied.append(str(target))

    ctx = get_runtime_context()
    video_items: list[dict[str, Any]] = []
    resolution_units: list[dict[str, Any]] = []
    for index in range(1, len(copied) + 1):
        source_name = _safe_name(names[index - 1] if index - 1 < len(names) else videos[index - 1].name)
        unit_id = f"{run_id}:video:{index:02d}"
        evidence = list(evidence_sets[index - 1]) if index - 1 < len(evidence_sets) else []
        for value in (source_name, str(videos[index - 1])):
            if value and value not in evidence:
                evidence.append(value)
        resolution_units.append(
            {
                "unit_id": unit_id,
                "item_index": index,
                "group_key": f"video:{index:04d}",
                "source_name": source_name,
                "evidence": evidence,
            }
        )
        video_items.append(
            {
                "index": index,
                "item_id": f"{run_id}:video-item:{index:04d}",
                "character_unit_id": unit_id,
                "character_group_key": f"video:{index:04d}",
                "source_path": str(videos[index - 1]),
                "source_name": source_name,
                "original_path": copied[index - 1],
                "name": Path(copied[index - 1]).name,
                "frame_dir": str(frames_dir / f"video_{index:02d}"),
                "matte_dir": str(matte_dir / f"video_{index:02d}"),
                "smooth_dir": str(smooth_dir / f"video_{index:02d}"),
                "frame_count": 0,
                "aspect": "",
                "cherry_profile": "",
                "cherry_output_size": "",
            }
        )
    run = {
        "id": run_id,
        "status": "RUNNING",
        "stage": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "run_label": run_label or f"{len(copied)} 个动画视频",
        "chat_id": (ctx.get("chat_id") or "") if ctx.get("channel") == "feishu" else "",
        "conversation_id": ctx.get("conversation_id") or "",
        "user_id": (ctx.get("open_id") or ctx.get("user_id") or "") if ctx.get("channel") == "feishu" else "",
        "videos": video_items,
        "children": {},
        "fps": int(fps),
        "max_frames": int(max_frames or 0),
        "workflow_path": workflow_path or "",
        "matting_backend": str(matting_backend or "").strip().lower(),
        "pipeline_notice": pipeline_notice,
        "notify_interval_seconds": max(30, min(int(notify_interval_seconds or 60), 3600)),
        "zip_path": "",
        "integrity": {},
        "error": "",
        "log": [],
    }
    from assetclaw_matting.services.character_resolution import (
        bind_run_items,
        initialize_run_resolutions,
    )

    character_state = initialize_run_resolutions(
        run_kind="direct_video",
        run_id=run_id,
        run_dir=run_dir,
        conversation_id=str(run.get("conversation_id") or ""),
        chat_id=str(run.get("chat_id") or ""),
        user_id=str(run.get("user_id") or ""),
        units=resolution_units,
    )
    bind_run_items("direct_video", run_id, video_items)
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
        return {"ok": False, "error": "direct video run not found"}
    return {"ok": True, "run_id": run["id"], **_public(run)}


def list_runs(limit: int = 10, include_finished: bool = True, **_: Any) -> dict[str, Any]:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    conversation_id = _current_feishu_conversation_id()
    for path in sorted(RUNS_ROOT.glob("VID_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        run = json.loads(path.read_text(encoding="utf-8"))
        if conversation_id and str(run.get("conversation_id") or "") != conversation_id:
            continue
        if run.get("status") in FINISHED and not include_finished:
            continue
        items.append({"run_id": run["id"], **_public(run)})
        if len(items) >= max(1, min(int(limit), 50)):
            break
    return {"ok": True, "count": len(items), "items": items}


def recover_incomplete_runs() -> dict[str, Any]:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    recovered: list[str] = []
    still_running: list[str] = []
    waiting_character: list[str] = []
    for status_path in sorted(RUNS_ROOT.glob("VID_*/status.json"), key=lambda path: path.stat().st_mtime):
        try:
            run = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        current_status = str(run.get("status") or "")
        if current_status == "WAITING_CHARACTER":
            run_id = str(run.get("id") or status_path.parent.name)
            from assetclaw_matting.services.character_resolution import (
                all_run_units_frozen,
                mark_run_waiting,
                reconcile_pending_evidence,
                reconcile_resolved_units,
            )

            mark_run_waiting("direct_video", run_id, immediate_if_uninitialized=True)
            reconcile_pending_evidence("direct_video", run_id)
            reconcile_resolved_units("direct_video", run_id)
            if all_run_units_frozen("direct_video", run_id):
                resume_after_character_resolution(run_id)
                recovered.append(run_id)
            else:
                waiting_character.append(run_id)
            continue
        if current_status == "FAILED":
            run_id = str(run.get("id") or status_path.parent.name)
            child_id = str((run.get("children") or {}).get("comfyui_run_id") or "")
            child_status = _comfyui_child_status(child_id)
            recovered_manifest_result = (
                "result manifest fields do not match" in str(run.get("error") or "").lower()
                and str(child_status.get("status") or "").upper() == "DONE"
            )
            if recovered_manifest_result:
                from assetclaw_matting.services.character_resolution import reopen_failed_run_resolutions

                reopened = reopen_failed_run_resolutions("direct_video", run_id)
                run["recovery_from_stage"] = "matting"
                run["status"] = "QUEUED"
                run["stage"] = "recovery_queued"
                run["worker_pid"] = 0
                run["error"] = ""
                run.setdefault("children", {})["comfyui"] = child_status
                _append_log(run, "GPU 结果已重新验收通过，从后处理继续，不重复提交 121 帧 GPU 批次。")
                if reopened:
                    _append_log(run, "已恢复因技术故障关闭的角色确认，不需要用户重新上传文件。")
                _save(run)
                _start_worker(run_id, recover=True)
                recovered.append(run_id)
                continue
        if current_status not in {"RUNNING", "QUEUED", "PENDING"}:
            continue
        worker_pid = int(run.get("worker_pid") or 0)
        if worker_pid and _process_alive(worker_pid):
            still_running.append(str(run.get("id") or ""))
            continue
        _append_log(run, "检测到机器人进程曾重启：从原视频安全恢复未完成任务")
        run["recovery_from_stage"] = str(run.get("stage") or "")
        run["status"] = "QUEUED"
        run["stage"] = "recovery_queued"
        run["worker_pid"] = 0
        _save(run)
        _start_worker(str(run["id"]), recover=True)
        recovered.append(str(run["id"]))
    return {"ok": True, "recovered": recovered, "still_running": still_running, "waiting_character": waiting_character}


def resend_zip(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct video run not found"}
    zip_path_text = str(run.get("zip_path") or "")
    if not zip_path_text:
        return {"ok": False, "error": "zip_path is empty"}
    zip_path = Path(zip_path_text)
    if not zip_path.exists():
        return {"ok": False, "error": f"zip not found: {zip_path}"}
    _verify_zip(zip_path)
    _send_zip_with_retries(run, zip_path)
    run["status"] = "DONE"
    run["stage"] = "done"
    run["error"] = ""
    _append_log(run, f"zip 重发完成：{zip_path.name}，{zip_path.stat().st_size} bytes")
    _save(run)
    return {"ok": True, "run_id": run["id"], "zip_path": str(zip_path), "zip_size": zip_path.stat().st_size}


def deliver_matte_only(
    run_id: str,
    resend: bool = True,
    _allow_active: bool = False,
    **_: Any,
) -> dict[str, Any]:
    """Package and deliver verified transparent matte frames without Cherry.

    This is the explicit operator escape hatch for a task whose matting result
    is complete but whose character reference is unavailable.  It never
    submits GPU work and never fabricates a color-correction result.
    """

    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct video run not found"}
    if not _allow_active and str(run.get("status") or "").upper() in {"RUNNING", "QUEUED", "PENDING"}:
        return {"ok": False, "run_id": run_id, "error": "task is still running; matte-only delivery refused"}
    try:
        run["status"] = "RUNNING"
        run["stage"] = "matte_only_package"
        run["error"] = ""
        run["worker_pid"] = 0
        _append_log(run, "按操作员要求跳过角色校色与位置矫正，开始校验并打包透明抠图结果；不会重跑 GPU。")
        _save(run)
        zip_path = _make_matte_only_zip(run)
        run["zip_path"] = str(zip_path)
        run["result_mode"] = "matte_only"
        run["postprocess_skipped"] = {
            "value": True,
            "reason": "character reference unavailable; operator requested direct matte delivery",
            "recorded_at": _now(),
        }
        _save(run)
        if resend and run.get("chat_id"):
            run["stage"] = "matte_only_delivery"
            _save(run)
            _send_zip_with_retries(run, zip_path)
        from assetclaw_matting.services.character_resolution import cancel_run_resolutions

        cancel_run_resolutions("direct_video", str(run["id"]))
        run["character_question"] = ""
        run.setdefault("character_resolution", {})["pending"] = 0
        run["status"] = "DONE"
        run["stage"] = "done_matte_only"
        run["error"] = ""
        run["updated_at"] = _now()
        _append_log(run, f"透明抠图结果已直接交付：{zip_path.name}；角色校色与位置矫正未执行。")
        _save(run)
        if resend and run.get("chat_id"):
            _notify(
                run,
                f"抠图结果已交付：{run['id']}。\n"
                "已返回 1 个 ZIP，包内包含原始抽帧、透明抠图和后处理目录。\n"
                "未生成后处理结果：角色库中暂无对应角色的校色与位置矫正资料。",
            )
        return {"ok": True, "run_id": run_id, **_public(run)}
    except Exception as exc:
        run = _load(run_id) or run
        run["status"] = "FAILED"
        run["stage"] = "matte_only_delivery_failed"
        run["error"] = str(exc)
        run["updated_at"] = _now()
        _append_log(run, f"透明抠图结果直接交付失败：{exc}")
        _save(run)
        return {"ok": False, "run_id": run_id, **_public(run)}


def repair_from_frames(run_id: str, resend: bool = True) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct video run not found"}
    try:
        for item in run.get("videos") or []:
            original = Path(str(item.get("original_path") or ""))
            if not original.is_file():
                raise RuntimeError(f"cannot fully repair without original video: {original}")
            if not item.get("source_name"):
                item["source_name"] = _source_display_name(run, item)
        backup_dir = _archive_previous_results(run)
        run["status"] = "RUNNING"
        run["stage"] = "repair_extract_frames"
        run["error"] = ""
        run["children"] = {"repair_backup": str(backup_dir)}
        run["integrity"] = {}
        run["zip_path"] = ""
        _append_log(run, f"开始红线修复：从原视频重新抽帧、抠图和后处理，旧结果已归档到 {backup_dir.name}")
        _save(run)
        _extract_all(run)
        _mark(run, "RUNNING", "repair_matting")
        _run_comfyui(run)
        if not _prepare_character_gate(run):
            return {"ok": True, "run_id": run_id, **_public(run)}
        _mark(run, "RUNNING", "repair_postprocess")
        _run_cherry(run)
        _mark(run, "RUNNING", "repair_zip")
        zip_path = _make_zip(run)
        run["zip_path"] = str(zip_path)
        if resend and run.get("chat_id"):
            _mark(run, "RUNNING", "repair_delivery")
            _send_zip_with_retries(run, zip_path)
        run["status"] = "DONE"
        run["stage"] = "done"
        run["error"] = ""
        _append_log(run, f"红线修复完成：{zip_path.name}，逐帧校验及打包闸门均通过")
        _save(run)
        return {"ok": True, "run_id": run_id, **_public(run)}
    except Exception as exc:
        run = _load(run_id) or run
        run["status"] = "FAILED"
        run["stage"] = "repair_failed"
        run["error"] = str(exc)
        _append_log(run, f"红线修复失败并停止发送：{exc}")
        _save(run)
        return {"ok": False, "run_id": run_id, **_public(run)}


def resume_from_postprocess(run_id: str, resend: bool = True) -> dict[str, Any]:
    """Reuse verified matte frames, retry Cherry, package, and deliver."""

    from assetclaw_matting.config import settings
    from assetclaw_matting.db.sqlite import init_db

    init_db(settings.data_db_path)
    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct video run not found"}
    try:
        if not _prepare_character_gate(run):
            return {"ok": True, "run_id": run_id, **_public(run)}
        for item in run.get("videos") or []:
            validate_matte_sequence(item["frame_dir"], item["matte_dir"])
        run["status"] = "RUNNING"
        run["stage"] = "resume_postprocess"
        run["error"] = ""
        _append_log(run, "复用已通过红线校验的抠图帧，从 Cherry 后处理阶段恢复；不重复抽帧或抠图")
        _save(run)
        _run_cherry(run)
        _mark(run, "RUNNING", "resume_zip")
        zip_path = _make_zip(run)
        run["zip_path"] = str(zip_path)
        if resend and run.get("chat_id"):
            _mark(run, "RUNNING", "resume_delivery")
            plan = _cherry_plan_summary(run.get("videos") or [])
            suffix = f"，{plan}" if plan else ""
            _notify(run, f"动画恢复完成：{run['id']}，正在重新发送 zip{suffix}。")
            _send_zip_with_retries(run, zip_path)
        run["status"] = "DONE"
        run["stage"] = "done"
        run["error"] = ""
        run["updated_at"] = _now()
        _append_log(run, f"Cherry 恢复、完整性校验、打包和发送完成：{zip_path.name}")
        _save(run)
        return {"ok": True, "run_id": run_id, **_public(run)}
    except Exception as exc:
        run = _load(run_id) or run
        run["status"] = "FAILED"
        run["stage"] = "resume_postprocess_failed"
        run["error"] = str(exc)
        run["updated_at"] = _now()
        _append_log(run, f"Cherry 恢复在自动重试后仍失败：{exc}")
        _save(run)
        _notify(run, _user_failure_notice(run_id, exc))
        return {"ok": False, "run_id": run_id, **_public(run)}


def resume_interrupted_run(run_id: str, resend: bool = True) -> dict[str, Any]:
    """Resume the persisted child/batch instead of rebuilding an intact task."""

    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct video run not found"}
    previous_stage = str(run.get("recovery_from_stage") or run.get("stage") or "").lower()
    try:
        zip_path_text = str(run.get("zip_path") or "")
        if previous_stage in {"delivery", "resume_delivery", "repair_delivery"} and zip_path_text and Path(zip_path_text).is_file():
            return resend_zip(run_id)
        if any(token in previous_stage for token in ("postprocess", "cherry", "smooth")):
            return resume_from_postprocess(run_id, resend=resend)
        if "matting" in previous_stage or "comfy" in previous_stage:
            _mark(run, "RUNNING", "recovery_matting")
            _resume_existing_comfyui_child(run)
            return resume_from_postprocess(run_id, resend=resend)
        if any(token in previous_stage for token in ("zip", "pack")):
            for item in run.get("videos") or []:
                validate_sequence_names(item["matte_dir"], item["smooth_dir"], label="matte_to_smooth")
            zip_path = _make_zip(run)
            run["zip_path"] = str(zip_path)
            _save(run)
            if resend and run.get("chat_id"):
                _mark(run, "RUNNING", "recovery_delivery")
                _send_zip_with_retries(run, zip_path)
            run["status"] = "DONE"
            run["stage"] = "done"
            run["error"] = ""
            _append_log(run, f"从持久化后处理结果恢复打包与发送：{zip_path.name}")
            _save(run)
            return {"ok": True, "run_id": run_id, **_public(run)}

        _append_log(run, f"恢复点 {previous_stage or 'unknown'} 尚未创建远端批次，从原始业务输入继续执行")
        _save(run)
        _worker(run_id)
        completed = _load(run_id) or run
        return {"ok": completed.get("status") == "DONE", "run_id": run_id, **_public(completed)}
    except Exception as exc:
        latest = _load(run_id) or run
        latest["status"] = "FAILED"
        latest["stage"] = "recovery_failed"
        latest["error"] = str(exc)
        _append_log(latest, f"持久化恢复失败：{exc}")
        _save(latest)
        _notify(latest, _user_failure_notice(run_id, exc))
        return {"ok": False, "run_id": run_id, **_public(latest)}


def _resume_existing_comfyui_child(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.comfyui_skills import run_resume, run_status

    child_id = str((run.get("children") or {}).get("comfyui_run_id") or "")
    if not child_id:
        _append_log(run, "恢复点没有抠图子任务，创建新的幂等批次代次")
        _run_comfyui(run)
        return
    payload = run_status(child_id, include_gpu=False)
    run.setdefault("children", {})["comfyui"] = payload
    _save(run)
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
            raise RuntimeError(_format_comfyui_failure(child_id, payload))
        if status_text != "DONE":
            time.sleep(5)
    for item in run.get("videos") or []:
        key = f"video_{int(item.get('index') or 0):02d}"
        run.setdefault("integrity", {}).setdefault("matte", {})[key] = validate_matte_sequence(item["frame_dir"], item["matte_dir"])
    _append_log(run, f"已重新挂接持久化抠图子任务：{child_id}，逐帧校验通过")
    _save(run)


def _comfyui_child_status(run_id: str) -> dict[str, Any]:
    if not run_id:
        return {}
    try:
        from assetclaw_matting.skills.comfyui_skills import run_status

        return run_status(run_id, include_gpu=False)
    except Exception:
        return {}


def cancel(run_id: str | None = None, **_: Any) -> dict[str, Any]:
    run = _load(run_id)
    if not run:
        return {"ok": False, "error": "direct video run not found"}
    cancel_results = _cancel_child_runs(run)
    from assetclaw_matting.services.character_resolution import cancel_run_resolutions

    cancel_run_resolutions("direct_video", str(run["id"]))
    run["status"] = "CANCELED"
    run["stage"] = "canceled"
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
        return {"ok": False, "run_id": run_id, "error": "direct video run not found"}
    if not all_run_units_frozen("direct_video", run_id):
        return {"ok": False, "run_id": run_id, "status": run.get("status"), "error": "character resolution is incomplete"}
    if str(run.get("status") or "") != "WAITING_CHARACTER":
        return {"ok": True, "run_id": run_id, "status": run.get("status"), "scheduled": False}
    run["status"] = "QUEUED"
    run["stage"] = "character_resolved"
    run["recovery_from_stage"] = "postprocess"
    run["worker_pid"] = 0
    run["error"] = ""
    _append_log(run, "角色已全部确认，从已完成的抠图结果继续后处理；不会重复抽帧或抠图。")
    if not _save(run, expected_statuses={"WAITING_CHARACTER"}):
        latest = _load(run_id) or run
        return {"ok": True, "run_id": run_id, "status": latest.get("status"), "scheduled": False}
    scheduled = _start_worker(run_id, recover=True)
    return {"ok": True, "run_id": run_id, "status": "QUEUED", "scheduled": scheduled}


def fail_character_confirmation_timeout(run_id: str, reason: str) -> dict[str, Any]:
    """Terminally fail only a parent that is still at the character gate."""

    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct video run not found"}
    if str(run.get("status") or "") != "WAITING_CHARACTER":
        return {
            "ok": False,
            "run_id": run_id,
            "status": run.get("status"),
            "error": "direct video run is no longer waiting for character confirmation",
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
            "error": "direct video run changed while applying character timeout",
        }
    return {"ok": True, "run_id": run_id, "status": "FAILED", "stage": run["stage"]}


def preview_start_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    videos = arguments.get("video_paths") or []
    names = arguments.get("source_names") or []
    count = len(videos)
    lines = [
        f"收到 {count} 个动画视频，可以开始处理。",
    ]
    for index, path in enumerate(videos[:8], start=1):
        display = names[index - 1] if index - 1 < len(names) else Path(str(path)).name
        lines.append(f"{index}. {display}")
    if count > 8:
        lines.append(f"还有 {count - 8} 个视频也会一起处理。")
    lines.extend(
        [
            "流程：抽帧、抠图、后处理，完成后发 zip。",
            "后处理：正方形 256x256，长方形 384x512，自动判断。",
            "视频文件收到后会自动开始，无需再次确认。",
        ]
    )
    return "\n".join(lines)


def _worker(run_id: str) -> None:
    run = _load(run_id)
    if not run:
        return
    try:
        _mark(run, "RUNNING", "extract_frames")
        _extract_all(run)
        if _is_canceled(run):
            return

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

        _mark(run, "RUNNING", "zip")
        zip_path = _make_zip(run)
        run["zip_path"] = str(zip_path)
        _mark(run, "RUNNING", "delivery")
        plan = _cherry_plan_summary(run.get("videos") or [])
        suffix = f"，{plan}" if plan else ""
        _notify(
            run,
            f"动画处理完成：{run['id']}，正在发送 1 个完整 ZIP："
            f"包内包含原始抽帧、透明抠图、后处理结果{suffix}。",
        )
        _send_zip_with_retries(run, zip_path)
        run["status"] = "DONE"
        run["stage"] = "done"
        run["error"] = ""
        run["updated_at"] = _now()
        _append_log(run, f"完整结果包发送完成：{zip_path.name}，{zip_path.stat().st_size} bytes")
        _save(run)
        _notify(
            run,
            f"动画完成：{run['id']}。已返回 1 个 ZIP，包内包含原始抽帧、透明抠图、后处理结果。"
            f"{_character_completion_lines(run.get('videos') or [])}",
        )
    except Exception as exc:
        run = _load(run_id) or run
        if run.get("status") != "CANCELED":
            delivery_failed = run.get("stage") == "delivery" and Path(str(run.get("zip_path") or "")).is_file()
            run["status"] = "DONE_WITH_ERRORS" if delivery_failed else "FAILED"
            run["error"] = str(exc)
            run["updated_at"] = _now()
            if delivery_failed:
                _append_log(run, f"处理和打包已完成，仅文件发送失败：{exc}")
            else:
                _close_failed_character_resolution(run)
                _append_log(run, f"任务失败：{exc}")
            _save(run)
            if delivery_failed:
                _notify(run, f"动画处理和打包已完成，但自动发送多次未成功：{run_id}\n结果已保留，可直接重发，无需重新抽帧、抠图和后处理。\n{exc}")
            else:
                _notify(run, _user_failure_notice(run_id, exc))
    finally:
        _WORKERS.discard(run_id)


def _extract_all(run: dict[str, Any]) -> None:
    python = Path(settings.comfyui_python_exe)
    script = Path(settings.assetclaw_root) / "scripts" / "extract_direct_video_frames.py"
    for item in run["videos"]:
        if _is_canceled(run):
            return
        out_dir = Path(item["frame_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        _append_log(run, f"开始抽帧：{item['name']}")
        _save(run)
        proc = subprocess.Popen(
            [str(python), str(script), item["original_path"], str(out_dir), str(run["fps"]), str(run["max_frames"])],
            cwd=str(Path(settings.assetclaw_root)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        last_payload: dict[str, Any] = {}
        for line in proc.stdout:
            _handle_extract_log(run, line.strip())
            if line.strip().startswith("{"):
                try:
                    payload = json.loads(line)
                    if payload.get("ok"):
                        last_payload = payload
                except Exception:
                    pass
        stderr = proc.stderr.read() if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(stderr.strip() or f"extract worker exited with {rc}")
        item["frame_count"] = int(last_payload.get("frame_count") or len(list(out_dir.glob("*.png"))))
        width, height = _first_frame_size(out_dir)
        item["cherry_profile"] = _cherry_profile_from_dimensions(width, height)
        item["aspect"] = "square" if item["cherry_profile"] == "half" else "portrait"
        item["cherry_output_size"] = _cherry_output_size(str(item["cherry_profile"]))
        _append_log(
            run,
            f"抽帧完成：{item['name']}，{item['frame_count']} 帧，{width}x{height}，{item['aspect']}，后处理 {item['cherry_output_size']}",
        )
        run["updated_at"] = _now()
        _save(run)


def _run_comfyui(run: dict[str, Any]) -> None:
    from assetclaw_matting.services.hybrid_matting_router import local_pipeline_serialization_required

    requested_stage = str(run.get("stage") or "matting")
    if not local_pipeline_serialization_required():
        _mark(run, "RUNNING", requested_stage)
        _append_log(run, "混合抠图路由已启用：本业务任务保持完整，按本机/集群容量分配")
        _save(run)
        _run_comfyui_unlocked(run)
        return
    _mark(run, "RUNNING", "waiting_matting_queue")
    _append_log(run, "等待独占抠图队列：视频任务将逐个处理，禁止并发混帧")
    _save(run)
    with _COMFYUI_SEQUENCE_LOCK:
        with _cross_process_video_matting_lock(run):
            _mark(run, "RUNNING", requested_stage)
            _run_comfyui_unlocked(run)


def _run_comfyui_unlocked(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.comfyui_skills import run_start, run_status

    run_dir = _run_dir(run)
    repair_attempt = 0
    while True:
        matting_generation = int(run.get("matting_generation") or 0) + 1
        run["matting_generation"] = matting_generation
        _save(run)
        result = run_start(
            workflow_path=run.get("workflow_path") or None,
            input_dir=str(run_dir / "frames"),
            output_dir=str(run_dir / "matte"),
            recursive=True,
            preserve_structure=True,
            skip_existing=repair_attempt > 0,
            notify_interval_seconds=run["notify_interval_seconds"],
            strict_frame_identity=True,
            external_batch_id=f"assetclaw:{run['id']}:matting:g{matting_generation}",
            backend="gpu_control" if repair_attempt > 0 else (str(run.get("matting_backend") or "") or None),
        )
        child_id = result["run_id"]
        children = run.setdefault("children", {})
        children["comfyui_run_id"] = child_id
        children.setdefault("comfyui_run_ids", []).append(child_id)
        _append_log(
            run,
            f"{'GPU 失败帧补算' if repair_attempt else 'ComfyUI 抠图任务'}已启动：{child_id}",
        )
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
                integrity = run.setdefault("integrity", {}).setdefault("matte", {})
                for item in run.get("videos") or []:
                    key = f"video_{int(item.get('index') or 0):02d}"
                    integrity[key] = validate_matte_sequence(item["frame_dir"], item["matte_dir"])
                _append_log(run, "红线校验通过：全部抽帧与抠图逐帧同名、同内容、无串帧")
                _save(run)
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
                    f"GPU 批次部分成功：已保留 {audit['completed_preserved']} 帧；"
                    f"仅补算 {audit['failed']} 个失败帧（第 {repair_attempt}/{MAX_PARTIAL_MATTING_REPAIRS} 次）。",
                )
                _save(run)
                break
            raise RuntimeError(_format_comfyui_failure(child_id, payload))


def _run_cherry(run: dict[str, Any]) -> None:
    from assetclaw_matting.skills.cherry_skills import run_start, run_status

    run.setdefault("children", {})["cherry_run_ids"] = []
    for item in run["videos"]:
        matte_dir = Path(str(item["matte_dir"]))
        smooth_dir = Path(str(item["smooth_dir"]))
        if not any(matte_dir.rglob("*.png")):
            raise RuntimeError(f"matte_dir has no png images: {matte_dir}")
        profile = str(item.get("cherry_profile") or "").strip().lower()
        if profile not in {"full", "half"}:
            raise RuntimeError(f"Cherry output profile is missing or invalid for video item: {item.get('item_id') or item.get('index')}")
        reference_path = str(item.get("color_reference_path") or "")
        reference_sha256 = str(item.get("color_reference_sha256") or "").lower()
        if not reference_path or not reference_sha256:
            raise RuntimeError(f"character reference binding is incomplete for video item: {item.get('item_id') or item.get('index')}")
        item["cherry_output_size"] = item.get("cherry_output_size") or _cherry_output_size(profile)
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
        _append_log(run, f"Cherry 后处理任务已启动：{child_id}，video={item['index']}，profile={profile}")
        _save(run)
        while True:
            if _is_canceled(run):
                return
            payload = run_status(child_id, include_gpu=False)
            run["children"]["cherry"] = payload
            run["children"].setdefault("cherry_runs", {})[child_id] = payload
            _save(run)
            if payload.get("status") in {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
                if payload.get("status") in {"FAILED", "CANCELED", "DONE_WITH_ERRORS"}:
                    raise RuntimeError(f"Cherry run {child_id} ended as {payload.get('status')}")
                key = f"video_{int(item.get('index') or 0):02d}"
                report = validate_sequence_names(matte_dir, smooth_dir, label="matte_to_smooth")
                run.setdefault("integrity", {}).setdefault("smooth", {})[key] = report
                item["color_correction_status"] = "DONE"
                item["position_alignment_status"] = "DONE"
                item["reference_postprocess_status"] = "DONE"
                item["color_correction_run_id"] = child_id
                _append_log(run, f"后处理序列校验通过：{key}，{report['count']} 帧")
                _save(run)
                break
            time.sleep(5)


def _prepare_character_gate(run: dict[str, Any]) -> bool:
    from assetclaw_matting.services.character_resolution import bind_run_items

    result = bind_run_items("direct_video", str(run["id"]), list(run.get("videos") or []))
    if result.get("ready"):
        run.setdefault("character_resolution", {})["pending"] = 0
        run["character_question"] = ""
        _append_log(run, "角色参考图已逐视频冻结；Cherry 将在流程末尾依次执行校色和位置矫正。")
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

    mark_run_waiting("direct_video", str(run["id"]))
    return False


def _make_zip(run: dict[str, Any]) -> Path:
    run_dir = _run_dir(run)
    integrity = run.setdefault("integrity", {})
    matte_reports = integrity.setdefault("matte", {})
    smooth_reports = integrity.setdefault("smooth", {})
    for item in run.get("videos") or []:
        key = f"video_{int(item.get('index') or 0):02d}"
        matte_reports[key] = validate_matte_sequence(item["frame_dir"], item["matte_dir"])
        smooth_reports[key] = validate_sequence_names(item["matte_dir"], item["smooth_dir"], label="matte_to_smooth")
    integrity["package_gate"] = {
        "passed": True,
        "checked_at": _now(),
        "rule": "frames/N.png -> matte/N.png -> smooth/N.png must preserve the same ordered frame identity",
    }
    manifest = run_dir / "manifest.json"
    manifest.write_text(json.dumps(_public(run), ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = run_dir / _zip_filename(run)
    partial = zip_path.with_suffix(".zip.part")
    include_dirs = ["original_videos", "frames", "matte", "smooth"]
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            zf.write(manifest, arcname="manifest.json", compress_type=zipfile.ZIP_DEFLATED)
            _write_run_folders(zf, run_dir, include_dirs)
        _verify_zip(partial)
        os.replace(partial, zip_path)
    finally:
        partial.unlink(missing_ok=True)
    return zip_path


def _video_delivery_specs(run: dict[str, Any], *, zip_path: Path) -> list[dict[str, Any]]:
    """Return one delivery artifact containing every completed video stage."""

    specs = [{
        "kind": "complete_bundle",
        "label": "完整结果包（原始抽帧、透明抠图、后处理结果）",
        "path": str(zip_path),
    }]
    previous = {
        str(item.get("kind") or ""): item
        for item in run.get("delivery_artifacts") or []
        if isinstance(item, dict)
    }
    prepared: list[dict[str, Any]] = []
    for spec in specs:
        path = Path(spec["path"])
        prior = previous.get(spec["kind"], {})
        sha256 = _sha256_path(path)
        same_file = (
            str(prior.get("path") or "") == str(path)
            and int(prior.get("size_bytes") or 0) == path.stat().st_size
            and str(prior.get("sha256") or "") == sha256
        )
        prepared.append({
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
    run["delivery_artifacts"] = prepared
    run["sent_files"] = [item["path"] for item in prepared if item["status"] == "DELIVERED"]
    _save(run)
    return prepared


def _make_matte_only_zip(run: dict[str, Any]) -> Path:
    run_dir = _run_dir(run)
    integrity = run.setdefault("integrity", {})
    matte_reports = integrity.setdefault("matte", {})
    for item in run.get("videos") or []:
        key = f"video_{int(item.get('index') or 0):02d}"
        matte_reports[key] = validate_matte_sequence(item["frame_dir"], item["matte_dir"])
    integrity["matte_only_package_gate"] = {
        "passed": True,
        "checked_at": _now(),
        "rule": "frames/N.png -> matte/N.png must preserve every ordered frame identity",
    }
    manifest_payload = {
        **_public(run),
        "result_mode": "matte_only",
        "postprocess_applied": False,
        "notice": "透明抠图结果；未执行角色校色与位置矫正。",
    }
    manifest = run_dir / "matte_only_manifest.json"
    manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    videos = run.get("videos") or []
    source_name = str((videos[0] if videos else {}).get("source_name") or run.get("run_label") or run["id"])
    stem = Path(_safe_name(source_name)).stem or str(run["id"])
    zip_path = run_dir / f"{stem}_matte_only.zip"
    partial = zip_path.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.write(manifest, arcname="manifest.json", compress_type=zipfile.ZIP_DEFLATED)
            _write_run_folders(archive, run_dir, ("original_videos", "frames", "matte"))
            archive.writestr(
                "smooth/README.txt",
                "未生成后处理结果：角色库中暂无对应角色的校色与位置矫正资料。\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
        _verify_zip(partial)
        os.replace(partial, zip_path)
    finally:
        partial.unlink(missing_ok=True)
    return zip_path


def _write_run_folders(
    archive: zipfile.ZipFile,
    run_dir: Path,
    folders: list[str] | tuple[str, ...],
) -> None:
    """Add run outputs deterministically without recompressing PNG/video data."""

    already_compressed = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv"}
    for folder in folders:
        root = run_dir / folder
        if not root.exists():
            continue
        for path in sorted(root.rglob("*"), key=lambda value: str(value).lower()):
            if not path.is_file():
                continue
            compression = zipfile.ZIP_STORED if path.suffix.lower() in already_compressed else zipfile.ZIP_DEFLATED
            archive.write(
                path,
                arcname=str(path.relative_to(run_dir)).replace("\\", "/"),
                compress_type=compression,
            )


def _verify_zip(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        damaged = archive.testzip()
    if damaged:
        raise RuntimeError(f"zip integrity check failed: {damaged}")


def _archive_previous_results(run: dict[str, Any]) -> Path:
    run_dir = _run_dir(run).resolve()
    backup_dir = (run_dir / "repairs" / datetime.now().strftime("%Y%m%d_%H%M%S")).resolve()
    if run_dir not in backup_dir.parents:
        raise RuntimeError(f"unsafe repair backup path: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=False)
    candidates: list[Path] = [run_dir / "frames", run_dir / "matte", run_dir / "smooth", run_dir / "manifest.json"]
    current_zip = Path(str(run.get("zip_path") or "")) if run.get("zip_path") else None
    if current_zip and current_zip.resolve().parent == run_dir:
        candidates.append(current_zip)
    candidates.extend(run_dir.glob("*_animation_processed.zip"))
    for artifact in run.get("delivery_artifacts") or []:
        artifact_path = Path(str(artifact.get("path") or "")) if isinstance(artifact, dict) else None
        if artifact_path and artifact_path.is_file():
            candidates.append(artifact_path)
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        if resolved.parent != run_dir and run_dir not in resolved.parents:
            raise RuntimeError(f"unsafe repair source path: {resolved}")
        shutil.move(str(path), str(backup_dir / path.name))
    for item in run.get("videos") or []:
        item["frame_count"] = 0
        Path(str(item["frame_dir"])).mkdir(parents=True, exist_ok=True)
        Path(str(item["matte_dir"])).mkdir(parents=True, exist_ok=True)
        Path(str(item["smooth_dir"])).mkdir(parents=True, exist_ok=True)
    return backup_dir


@contextmanager
def _cross_process_video_matting_lock(run: dict[str, Any]):
    import msvcrt

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = RUNS_ROOT / ".video_matting.lock"
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if _is_canceled(run):
                    raise RuntimeError(f"video matting queue canceled: {run['id']}")
                time.sleep(2)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _cross_process_video_pipeline_lock(run: dict[str, Any]):
    import msvcrt

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = RUNS_ROOT / ".video_pipeline.lock"
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if _is_canceled(run):
                    raise RuntimeError(f"video pipeline queue canceled: {run['id']}")
                latest = _load(str(run["id"])) or run
                if latest.get("stage") != "waiting_pipeline_queue":
                    latest["stage"] = "waiting_pipeline_queue"
                    _append_log(latest, "等待独占视频流水线：前一个视频完成后自动继续")
                    _save(latest)
                time.sleep(2)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _send_zip(run: dict[str, Any], zip_path: Path) -> dict[str, str]:
    chat_id = str(run.get("chat_id") or "")
    if not chat_id:
        raise RuntimeError("zip delivery requires a Feishu chat_id")
    from assetclaw_matting.feishu.client import feishu_client

    _append_log(run, f"开始发送 zip：{zip_path.name}，{zip_path.stat().st_size} bytes")
    _save(run)
    sent = feishu_client.send_file_to_chat(chat_id, zip_path, zip_path.name) or {}
    message_id = str(sent.get("message_id") or "")
    if not message_id:
        raise RuntimeError(f"Feishu delivery returned no message receipt for {zip_path.name}")
    if sent.get("file_token") or sent.get("url"):
        run["drive_file"] = sent
    run["delivery"] = {
        "status": "DELIVERED",
        "chat_id": chat_id,
        "file_name": zip_path.name,
        "file_size": zip_path.stat().st_size,
        "message_id": message_id,
        "file_token": str(sent.get("file_token") or ""),
        "url": str(sent.get("url") or ""),
        "delivery_method": str(sent.get("delivery_method") or ""),
        "delivered_at": _now(),
        "last_error": "",
    }
    _append_log(run, f"飞书交付回执已确认：{zip_path.name}，message_id={message_id}")
    _save(run)
    return sent


def _send_zip_with_retries(run: dict[str, Any], zip_path: Path, attempts: int = 5) -> None:
    if not zip_path.is_file():
        raise RuntimeError(f"complete result bundle is missing: {zip_path}")
    artifacts = _video_delivery_specs(run, zip_path=zip_path)
    from assetclaw_matting.feishu.client import feishu_client

    chat_id = str(run.get("chat_id") or "")
    if not chat_id:
        raise RuntimeError("video result delivery requires a Feishu chat_id")
    for artifact in artifacts:
        if artifact.get("status") == "DELIVERED" and artifact.get("message_id"):
            continue
        errors: list[str] = []
        for attempt in range(1, max(1, attempts) + 1):
            artifact["status"] = "UPLOADING"
            artifact["attempts"] = attempt
            artifact["last_error"] = ""
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
                run["sent_files"] = [item["path"] for item in artifacts if item.get("status") == "DELIVERED"]
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
    run["delivery"] = {
        "status": "DELIVERED",
        "chat_id": chat_id,
        "artifact_count": len(artifacts),
        "delivered_count": len(artifacts),
        "complete_bundle_path": str(zip_path),
        "delivered_at": _now(),
        "last_error": "",
    }
    _save(run)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _handle_extract_log(run: dict[str, Any], raw: str) -> None:
    if not raw:
        return
    try:
        payload = json.loads(raw)
        message = str(payload.get("message") or payload)
    except Exception:
        message = raw
    _append_log(run, message)
    _save(run)


def _format_progress(run: dict[str, Any]) -> str:
    children = run.get("children") or {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    lines = [
        f"动画处理进度：{run['id']}",
        f"状态：{run.get('status')} / {run.get('stage')}",
        f"视频：{len(run.get('videos') or [])} 个",
    ]
    frame_total = sum(int(item.get("frame_count") or 0) for item in run.get("videos") or [])
    if frame_total:
        lines.append(f"已抽帧：{frame_total} 张")
    if comfy:
        lines.append(f"抠图：{comfy.get('completed', 0)}/{comfy.get('total', 0)}，{comfy.get('status')}")
    if cherry:
        lines.append(f"后处理：{cherry.get('completed', 0)}/{cherry.get('total', 0)}，{cherry.get('status')}")
    log = run.get("log") or []
    if log:
        lines.append(f"最近：{log[-1].get('message')}")
    return "\n".join(lines)


def _format_stage_summary(run: dict[str, Any], title: str) -> str:
    parts = [f"{title}：{run['id']}"]
    details = [f"视频 {len(run.get('videos') or [])}"]
    frame_total = sum(int(item.get("frame_count") or 0) for item in run.get("videos") or [])
    if frame_total:
        details.append(f"抽帧 {frame_total}")
    children = run.get("children") or {}
    comfy = children.get("comfyui") if isinstance(children.get("comfyui"), dict) else {}
    if comfy:
        details.append(f"抠图 {comfy.get('completed', 0)}/{comfy.get('total', 0)}")
    cherry = children.get("cherry") if isinstance(children.get("cherry"), dict) else {}
    if cherry:
        details.append(f"后处理 {cherry.get('completed', 0)}/{cherry.get('total', 0)}")
    cherry_plan = _cherry_plan_summary(run.get("videos") or [])
    if cherry_plan:
        details.append(cherry_plan)
    if details:
        parts.append("，".join(details))
    return "；".join(parts)


def _format_comfyui_failure(run_id: str, payload: dict[str, Any]) -> str:
    detail = [
        f"ComfyUI run {run_id} ended as {payload.get('status')}",
        f"completed={payload.get('completed', 0)}/{payload.get('total', 0)}",
        f"failed={payload.get('failed', 0)}",
    ]
    if payload.get("last_completed"):
        detail.append(f"last_completed={payload.get('last_completed')}")
    if payload.get("last_error"):
        detail.append(f"last_error={payload.get('last_error')}")
    if payload.get("error"):
        detail.append(f"error={payload.get('error')}")
    return "；".join(detail)


def _user_failure_notice(run_id: str, error: Exception | str) -> str:
    detail = str(error or "")
    if "result manifest fields do not match" in detail.lower():
        reason = "抠图计算已经完成，但集群返回结果的协议校验未通过，因此没有交付错误文件。"
    else:
        reason = "任务未能完成，详细技术原因已经记录在后台日志。"
    return f"动画处理任务未完成：{run_id}\n{reason}\n系统不会交付不完整结果。"


def _close_failed_character_resolution(run: dict[str, Any]) -> None:
    from assetclaw_matting.services.character_resolution import fail_run_resolutions

    fail_run_resolutions("direct_video", str(run.get("id") or ""))
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


def _start_worker(run_id: str, recover: bool = False) -> bool:
    if run_id in _WORKERS:
        return False
    run = _load(run_id)
    if not run:
        return False
    existing_pid = int(run.get("worker_pid") or 0)
    if existing_pid and _process_alive(existing_pid):
        return False
    _WORKERS.add(run_id)
    script = Path(settings.assetclaw_root) / "scripts" / "direct_video_worker.py"
    log_path = Path(settings.log_dir) / f"direct_video_worker_{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", str(script), run_id]
    if recover:
        command.append("--recover")
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200 | 0x08000000
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    with log_path.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(Path(settings.assetclaw_root)),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
            env=child_env,
        )
    run["worker_pid"] = process.pid
    run["worker_mode"] = "detached"
    _append_log(run, f"持久化工作进程已启动：PID={process.pid}，机器人重启不会中断")
    _save(run)
    _WORKERS.discard(run_id)
    return True


def run_worker_process(run_id: str, recover: bool = False) -> dict[str, Any]:
    from assetclaw_matting.services.hybrid_matting_router import local_pipeline_serialization_required

    run = _load(run_id)
    if not run:
        return {"ok": False, "run_id": run_id, "error": "direct video run not found"}
    run["worker_pid"] = os.getpid()
    run["worker_mode"] = "detached_recovery" if recover else "detached"
    _save(run)
    try:
        if not local_pipeline_serialization_required():
            if recover:
                return resume_interrupted_run(run_id, resend=True)
            _worker(run_id)
            completed = _load(run_id) or run
            return {"ok": completed.get("status") == "DONE", "run_id": run_id, **_public(completed)}
        with _cross_process_video_pipeline_lock(run):
            if recover:
                return resume_interrupted_run(run_id, resend=True)
            _worker(run_id)
            completed = _load(run_id) or run
            return {"ok": completed.get("status") == "DONE", "run_id": run_id, **_public(completed)}
    finally:
        latest = _load(run_id)
        if latest and int(latest.get("worker_pid") or 0) == os.getpid():
            latest["worker_pid"] = 0
            _save(latest)
        _restart_character_resume_if_needed(run_id)


def _restart_character_resume_if_needed(run_id: str) -> None:
    latest = _load(run_id)
    if not latest or str(latest.get("status") or "") != "QUEUED":
        return
    if str(latest.get("stage") or "") != "character_resolved":
        return
    from assetclaw_matting.services.character_resolution import all_run_units_frozen

    if all_run_units_frozen("direct_video", run_id):
        _start_worker(run_id, recover=True)


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


def _is_canceled(run: dict[str, Any]) -> bool:
    latest = _load(run["id"])
    return bool(latest and latest.get("status") == "CANCELED")


def _mark(run: dict[str, Any], status_text: str, stage: str) -> None:
    run["status"] = status_text
    run["stage"] = stage
    run["updated_at"] = _now()
    _append_log(run, f"进入阶段：{stage}")
    _save(run)
    if stage == "canceled":
        _notify(run, f"任务已取消：{run['id']}")


def _append_log(run: dict[str, Any], message: str) -> None:
    message = _safe_display_text(str(message))
    logs = run.setdefault("log", [])
    logs.append({"ts": _now(), "message": message})
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
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "videos": run.get("videos") or [],
        "children": run.get("children") or {},
        "fps": run.get("fps"),
        "zip_path": run.get("zip_path") or "",
        "delivery": run.get("delivery") or {},
        "delivery_artifacts": run.get("delivery_artifacts") or [],
        "sent_files": run.get("sent_files") or [],
        "result_mode": run.get("result_mode") or "full",
        "postprocess_skipped": run.get("postprocess_skipped") or {},
        "drive_file": run.get("drive_file") or {},
        "character_question": "" if terminal else (run.get("character_question") or ""),
        "character_resolution": character_resolution,
        "integrity": run.get("integrity") or {},
        "repair_batch": run.get("repair_batch") or {},
        "worker_pid": int(run.get("worker_pid") or 0),
        "worker_mode": run.get("worker_mode") or "",
        "pipeline_notice": run.get("pipeline_notice") or "",
        "matting_backend": run.get("matting_backend") or "",
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
    lines: list[str] = []
    for item in items:
        name = str(item.get("source_name") or item.get("name") or "视频")
        character = str(item.get("character_id") or "未知角色")
        lines.append(f"{name} · {character} · 后处理（含校色/矫正）完成")
    return ("\n" + "\n".join(lines)) if lines else ""


def _load(run_id: str | None = None) -> dict[str, Any] | None:
    if run_id:
        path = RUNS_ROOT / run_id / "status.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        matched = _find_run_by_text(run_id)
        if matched:
            return matched
        return None
    if not RUNS_ROOT.exists():
        return None
    paths = sorted(RUNS_ROOT.glob("VID_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not paths:
        return None
    conversation_id = _current_feishu_conversation_id()
    active = []
    visible = []
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if conversation_id and str(run.get("conversation_id") or "") != conversation_id:
            continue
        visible.append(run)
        if run.get("status") not in FINISHED:
            active.append(run)
    return active[0] if active else (visible[0] if visible else None)


def _current_feishu_conversation_id() -> str:
    ctx = get_runtime_context()
    if str(ctx.get("channel") or "") != "feishu":
        return ""
    return str(ctx.get("conversation_id") or "")


def _safe_display_text(text: str) -> str:
    value = str(text or "")
    if "�" in value:
        return "任务状态已更新；详细技术日志仅保留在后台。"
    return value


def _find_run_by_text(value: str) -> dict[str, Any] | None:
    query = str(value or "").strip().lower()
    if not query or not RUNS_ROOT.exists():
        return None
    paths = sorted(RUNS_ROOT.glob("VID_*/status.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    fallback: dict[str, Any] | None = None
    for path in paths:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        haystack = " ".join(
            [str(run.get("id") or ""), str(run.get("run_label") or "")]
            + [str(item.get("name") or item.get("source_path") or item.get("original_path") or "") for item in run.get("videos") or []]
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
    return RUNS_ROOT / str(run["id"])


def _validate_video(path: str) -> Path:
    target = validate_path(path, must_exist=True)
    if not target.is_file():
        raise ValueError(f"video path must be a file: {target}")
    if target.suffix.lower() not in VIDEO_EXTS:
        raise ValueError(f"unsupported video extension: {target.suffix}")
    return target


def _safe_name(value: str) -> str:
    text = str(value or "").replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip(" .")
    return cleaned or "video.mp4"


def _zip_filename(run: dict[str, Any]) -> str:
    videos = run.get("videos") or []
    first = videos[0] if videos else {}
    source_name = str(first.get("source_name") or "").strip()
    if not source_name:
        source_name = str(run.get("run_label") or first.get("name") or run.get("id") or "animation")
    stem = Path(_safe_name(source_name)).stem
    if stem[:3].isdigit() and len(stem) > 3 and stem[2] == "_":
        stem = stem[3:]
    if len(videos) > 1:
        stem = f"{stem}_and_{len(videos)}_videos"
    stem = _safe_name(stem)[:120].rstrip(" .") or "animation"
    return f"{stem}_animation_processed.zip"


def _source_display_name(run: dict[str, Any], item: dict[str, Any]) -> str:
    source_name = str(item.get("source_name") or "").strip()
    if source_name:
        return _safe_name(source_name)
    label = str(run.get("run_label") or "").strip()
    if label and "、" not in label:
        return _safe_name(label)
    name = _safe_name(str(item.get("name") or Path(str(item.get("original_path") or "video.mp4")).name))
    stem = Path(name).stem
    if stem[:3].isdigit() and len(stem) > 3 and stem[2] == "_":
        stem = stem[3:]
    return f"{stem}{Path(name).suffix}"


def _first_frame_size(folder: Path) -> tuple[int, int]:
    first = next(iter(sorted(folder.glob("*.png"))), None)
    if not first:
        return 0, 0
    with Image.open(first) as img:
        return int(img.width), int(img.height)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


Kind = Literal["image", "video"]
TERMINAL = {"DONE", "FAILED", "CANCELED", "DONE_WITH_ERRORS"}
ACTIVE = {"RUNNING", "QUEUED", "PENDING", "WAITING_CHARACTER", "PAUSED", "PREPARING"}
_REQUEST_LOCK = threading.Lock()


def request_full_rerun(kind: Kind, run_id: str | None, *, confirmed: bool = False) -> dict[str, Any]:
    """Restart a terminal direct task in place after explicit confirmation.

    The run id, list position, source files and Feishu recipient stay unchanged.
    The previous terminal state is appended to ``rerun_history`` before output
    directories and runtime fields are reset.
    """

    if not confirmed:
        return {"ok": False, "error": "完整重跑需要二次确认"}
    with _REQUEST_LOCK:
        return _request_full_rerun_locked(kind, run_id)


def _request_full_rerun_locked(kind: Kind, run_id: str | None) -> dict[str, Any]:
    module = _module(kind)
    run = module._load(run_id)
    if not run:
        return {"ok": False, "error": f"direct {kind} run not found"}

    current_status = str(run.get("status") or "").upper()
    current_rerun = dict(run.get("rerun") or {})
    if current_status in ACTIVE and str(current_rerun.get("request_id") or ""):
        return {
            "ok": True,
            "already_running": True,
            "run_id": str(run["id"]),
            "rerun": rerun_state(kind, run),
        }
    if current_status not in TERMINAL:
        return {"ok": False, "error": "任务仍在处理，请结束后再完整重跑"}

    request_id = "RERUN_" + uuid.uuid4().hex[:12].upper()
    requested_at = _now()
    try:
        _validate_originals(kind, run)
        workflow = _preflight_latest_workflow(run)
        history = list(run.get("rerun_history") or [])
        history.append(_history_entry(run, request_id, requested_at))
        run["rerun_history"] = history[-20:]
        _reset_run(kind, module, run)
        if workflow:
            run["workflow_path"] = str(workflow.get("workflow_path") or run.get("workflow_path") or "")
            run["pipeline_notice"] = str(workflow.get("message") or "")
        run["status"] = "QUEUED"
        run["stage"] = "full_rerun_queued"
        run["error"] = ""
        run["worker_pid"] = 0
        run["rerun"] = {
            "request_id": request_id,
            "attempt": len(history),
            "status": "QUEUED",
            "stage": "full_rerun_queued",
            "requested_at": requested_at,
            "updated_at": _now(),
            "error": "",
            "in_place": True,
        }
        module._append_log(run, "已确认完整重跑：保留任务 ID 与历史记录，并从原始素材重新执行全部阶段。")
        module._save(run)

        started = module._start_worker(str(run["id"]))
        if started is False:
            raise RuntimeError("完整重跑 worker 未启动；任务可能已有活动进程")
        latest = module._load(str(run["id"])) or run
        return {"ok": True, "run_id": str(run["id"]), "rerun": rerun_state(kind, latest)}
    except Exception as exc:
        latest = module._load(str(run.get("id") or run_id or "")) or run
        # Only convert the task to a rerun failure if this request already
        # replaced its state. Preflight errors leave the original terminal
        # state intact and are returned to the operator.
        if str((latest.get("rerun") or {}).get("request_id") or "") == request_id:
            latest["status"] = "FAILED"
            latest["stage"] = "full_rerun_start_failed"
            latest["error"] = str(exc)
            latest["worker_pid"] = 0
            latest["rerun"] = {
                **dict(latest.get("rerun") or {}),
                "status": "FAILED",
                "stage": "full_rerun_start_failed",
                "updated_at": _now(),
                "finished_at": _now(),
                "error": str(exc),
            }
            module._append_log(latest, f"完整重跑启动失败：{exc}")
            module._save(latest)
        return {"ok": False, "run_id": str(run.get("id") or run_id or ""), "error": str(exc)}


def rerun_state(kind: Kind, run: dict[str, Any]) -> dict[str, Any]:
    """Return the latest in-place rerun state reconciled with the parent run."""

    state = dict(run.get("rerun") or {})
    if not state:
        return {}
    status = str(run.get("status") or state.get("status") or "").upper()
    return {
        **state,
        "status": status,
        "stage": str(run.get("stage") or state.get("stage") or ""),
        "updated_at": str(run.get("updated_at") or state.get("updated_at") or ""),
        "error": str(run.get("error") or "") if status in {"FAILED", "DONE_WITH_ERRORS"} else "",
        "in_place": True,
    }


def _validate_originals(kind: Kind, run: dict[str, Any]) -> None:
    items = sorted(run.get("images" if kind == "image" else "videos") or [], key=lambda item: int(item.get("index") or 0))
    if not items:
        raise RuntimeError("原任务没有可重跑的原始素材")
    for item in items:
        path = Path(str(item.get("original_path") or ""))
        if not path.is_file():
            name = item.get("source_name") or item.get("name") or path.name or "素材"
            raise RuntimeError(f"原始素材缺失：{name}")


def _preflight_latest_workflow(run: dict[str, Any]) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills import matting_pipeline_skills

    workflow_path = str(run.get("workflow_path") or settings.comfyui_workflow_path or "")
    if Path(workflow_path).name != settings.matting_pipeline_workflow_name:
        return {}
    result = matting_pipeline_skills.ensure_latest_for_task()
    if not result.get("ok"):
        raise RuntimeError(matting_pipeline_skills.preflight_error(result))
    return result


def _history_entry(run: dict[str, Any], request_id: str, requested_at: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "archived_at": requested_at,
        "status": str(run.get("status") or ""),
        "stage": str(run.get("stage") or ""),
        "error": str(run.get("error") or ""),
        "created_at": str(run.get("created_at") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "children": _children_audit(run.get("children") or {}),
        "sent_files": list(run.get("sent_files") or []),
        "delivery": dict(run.get("delivery") or {}),
    }


def _children_audit(children: dict[str, Any]) -> dict[str, Any]:
    """Keep rerun history useful without copying large polling payloads."""

    result: dict[str, Any] = {}
    for key in ("comfyui_run_id", "comfyui_run_ids", "cherry_run_id", "cherry_run_ids"):
        value = children.get(key)
        if value:
            result[key] = value
    for key in ("comfyui", "cherry"):
        value = children.get(key)
        if not isinstance(value, dict):
            continue
        result[key] = {
            field: value.get(field)
            for field in ("run_id", "status", "stage", "completed", "total", "error", "last_error")
            if value.get(field) not in (None, "", [], {})
        }
    for key in ("comfyui_runs", "cherry_runs"):
        values = children.get(key)
        if not isinstance(values, dict):
            continue
        result[key] = {
            str(run_id): {
                field: payload.get(field)
                for field in ("status", "stage", "completed", "total", "error", "last_error")
                if isinstance(payload, dict) and payload.get(field) not in (None, "", [], {})
            }
            for run_id, payload in values.items()
        }
    return result


def _reset_run(kind: Kind, module: Any, run: dict[str, Any]) -> None:
    if kind == "image":
        module._ensure_original_images(run)
        module._reset_outputs_for_full_retry(run)
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
        run["sequence_zip_path"] = ""
        run["full_pipeline_retry_count"] = 0
        run["full_pipeline_recovery"] = {}
        run["local_oom_gpu_fallback"] = {}
    else:
        backup_dir = module._archive_previous_results(run)
        run["rerun_backup_dir"] = str(backup_dir)
        for item in run.get("videos") or []:
            for key in (
                "matte_result_path",
                "postprocessed_result_path",
                "comparison_path",
                "result_path",
                "color_correction_run_id",
            ):
                item[key] = ""
        run["zip_path"] = ""
        run["integrity"] = {}
    run["children"] = {}
    run["sent_files"] = []
    run["delivery"] = {}
    run["delivery_artifacts"] = []
    run["drive_file"] = {}
    run["result_mode"] = "full"
    run.pop("postprocess_skipped", None)


def _module(kind: Kind) -> Any:
    if kind == "image":
        from assetclaw_matting.skills import direct_image_skills

        return direct_image_skills
    if kind == "video":
        from assetclaw_matting.skills import direct_video_skills

        return direct_video_skills
    raise ValueError(f"unsupported rerun kind: {kind}")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

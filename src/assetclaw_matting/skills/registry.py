"""Skill Registry — central catalog and dispatcher for the Skill Gateway."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from assetclaw_matting.skills import (
    batch_skills,
    comfyui_skills,
    file_skills,
    future_skills,
    log_skills,
    queue_skills,
    worker_skills,
)

log = logging.getLogger(__name__)


def _f(name: str, desc: str, danger: str, confirm: bool, impl: bool, fn: Any) -> dict[str, Any]:
    return {
        "name": name, "description": desc,
        "danger_level": danger, "requires_confirmation": confirm,
        "implemented": impl, "fn": fn,
    }


SKILL_CATALOG: list[dict[str, Any]] = [
    # ── Batch management ──────────────────────────────────────────────────────
    _f("batch.create",    "Create a ComfyUI batch job from a directory",            "medium", False, True,  batch_skills.batch_create),
    _f("batch.start",     "Start a CREATED batch",                                  "low",    False, True,  batch_skills.batch_start),
    _f("batch.status",    "Get batch progress and status",                           "low",    False, True,  batch_skills.batch_status),
    _f("batch.list",      "List recent batches",                                     "low",    False, True,  batch_skills.batch_list),
    _f("batch.cancel",    "Cancel all QUEUED tasks in a batch",                      "medium", True,  True,  batch_skills.batch_cancel),
    # ── Queue / Worker ────────────────────────────────────────────────────────
    _f("queue.status",        "Global queue statistics",                             "low",  False, True,  queue_skills.queue_status),
    _f("task.status",         "Get details of a specific task",                      "low",  False, True,  worker_skills.task_status),
    _f("task.list_failed",    "List failed tasks (optionally by batch)",             "low",  False, True,  worker_skills.task_list_failed),
    _f("worker.status",       "Worker activity summary",                             "low",  False, True,  worker_skills.worker_status),
    # ── ComfyUI ───────────────────────────────────────────────────────────────
    _f("comfyui.status",  "Check ComfyUI availability",                             "low",  False, True,  comfyui_skills.comfyui_status),
    # ── File / Log ────────────────────────────────────────────────────────────
    _f("file.list_allowed", "List files under allowed root (metadata only)",        "low",  False, True,  file_skills.file_list_allowed),
    _f("log.tail",          "Read last N lines of gateway or worker log (sanitised)","low", False, True,  log_skills.log_tail),
    # ── Video / Frame (future) ────────────────────────────────────────────────
    _f("video.download_or_import", "Import video for processing",           "medium", False, False, future_skills.video_download_or_import),
    _f("video.extract_frames",     "Extract image sequence from video",     "medium", False, False, future_skills.video_extract_frames),
    _f("frames.rename_from_table", "Rename frames from spreadsheet",        "medium", False, False, future_skills.frames_rename_from_table),
    _f("frames.delete_bad_frames", "Delete rejected frames",                "high",   True,  False, future_skills.frames_delete_bad_frames),
    _f("frames.dedupe_similar",    "Remove near-duplicate frames",          "medium", True,  False, future_skills.frames_dedupe_similar),
    # ── Image post-process (future) ───────────────────────────────────────────
    _f("noise.cleanup",        "Clean up noise artefacts",                  "medium", False, False, future_skills.noise_cleanup),
    _f("image.package_review", "Package images for review",                 "low",    False, False, future_skills.image_package_review),
    # ── 3D / Animation (future) ───────────────────────────────────────────────
    _f("model3d.generate",               "Generate 3D model from images",            "high",   True,  False, future_skills.model3d_generate),
    _f("texture.apply",                  "Apply texture to a 3D asset",              "medium", True,  False, future_skills.texture_apply),
    _f("animation.state_machine.create", "Create animation state machine",           "medium", False, False, future_skills.animation_state_machine_create),
    _f("animation.kframe.create",        "Create animation keyframes",               "medium", False, False, future_skills.animation_kframe_create),
    _f("qa.review_effects",              "QA review of animation effects",           "low",    False, False, future_skills.qa_review_effects),
    # ── Engine / Pipeline (future) ────────────────────────────────────────────
    _f("asset.import_engine", "Import asset into game engine",             "high",   True,  False, future_skills.asset_import_engine),
    _f("resource.cleanup",    "Clean up intermediate resource files",      "high",   True,  False, future_skills.resource_cleanup),
    _f("p4.submit",           "Submit changelist to Perforce",             "high",   True,  False, future_skills.p4_submit),
    # ── Workflow ──────────────────────────────────────────────────────────────
    _f("workflow.run",  "Run arbitrary ComfyUI workflow by name",          "high",   True,  False, future_skills.workflow_run),
]

_SKILL_MAP: dict[str, dict[str, Any]] = {s["name"]: s for s in SKILL_CATALOG}


def get_manifest() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    return {
        "node_name": "AssetClaw Win3090 Skill Node",
        "machine_id": settings.worker_id,
        "runtime": "windows",
        "gpu": "RTX 3090 24GB",
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
        "gpu_task_concurrency": settings.gpu_task_concurrency,
        "comfyui_fake_mode": settings.comfyui_fake_mode,
        "available_workflows": ["matting_v1"],
        "available_skills": [
            {k: s[k] for k in ("name", "description", "danger_level",
                                "requires_confirmation", "implemented")}
            for s in SKILL_CATALOG
        ],
    }


def call_skill(
    name: str,
    arguments: dict[str, Any],
    requested_by: str = "api",
    request_id: str = "",
) -> dict[str, Any]:
    from assetclaw_matting.skills.auth import log_skill_call

    if not request_id:
        request_id = str(uuid.uuid4())[:8]

    skill_info = _SKILL_MAP.get(name)
    if skill_info is None:
        err = f"Unknown skill: {name!r}. Available: {sorted(_SKILL_MAP)}"
        log_skill_call(request_id, name, arguments, None, False, err, requested_by)
        return {"ok": False, "skill": name, "error": err, "message": err}

    if not skill_info["implemented"]:
        result = skill_info["fn"](**arguments) if arguments else skill_info["fn"]()
        log_skill_call(request_id, name, arguments, result, True, None, requested_by)
        return {"ok": True, "skill": name, "result": result, "message": "not_implemented"}

    try:
        result = skill_info["fn"](**arguments)
        log.info("Skill %s by=%s req=%s", name, requested_by, request_id)
        log_skill_call(request_id, name, arguments, result, True, None, requested_by)
        return {"ok": True, "skill": name, "result": result,
                "message": f"Skill {name} executed successfully"}
    except (ValueError, PermissionError) as exc:
        err = str(exc)
        log.warning("Skill %s rejected: %s", name, err)
        log_skill_call(request_id, name, arguments, None, False, err, requested_by)
        return {"ok": False, "skill": name, "error": err, "message": err}
    except Exception as exc:
        err = str(exc)
        log.exception("Skill %s unexpected error", name)
        log_skill_call(request_id, name, arguments, None, False, err, requested_by)
        return {"ok": False, "skill": name, "error": err, "message": f"Internal error: {err}"}

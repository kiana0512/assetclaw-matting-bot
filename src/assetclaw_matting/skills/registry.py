"""Skill Registry: central catalog + dispatcher for the Skill Gateway.

All skill calls go through call_skill() which:
1. Looks up the skill in SKILL_CATALOG.
2. Validates it is implemented.
3. Calls the underlying function.
4. Logs the call to skill_calls table (audit).
5. Returns a structured result dict.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

from assetclaw_matting.skills import (
    batch_skills,
    comfyui_skills,
    file_skills,
    future_skills,
    log_skills,
    worker_skills,
)

log = logging.getLogger(__name__)


# ── Catalog ───────────────────────────────────────────────────────────────────

# Each entry: name, description, danger_level (low/medium/high),
# requires_confirmation, implemented, fn
SKILL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "batch.create",
        "description": "Create a batch matting job from an input directory",
        "danger_level": "medium",
        "requires_confirmation": False,
        "implemented": True,
        "fn": batch_skills.batch_create,
    },
    {
        "name": "batch.start",
        "description": "Start a CREATED batch so the worker picks up tasks",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": batch_skills.batch_start,
    },
    {
        "name": "batch.status",
        "description": "Get the current status and progress of a batch",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": batch_skills.batch_status,
    },
    {
        "name": "batch.list",
        "description": "List recent batches with status summary",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": batch_skills.batch_list,
    },
    {
        "name": "batch.cancel",
        "description": "Cancel all QUEUED tasks in a batch",
        "danger_level": "medium",
        "requires_confirmation": False,
        "implemented": True,
        "fn": batch_skills.batch_cancel,
    },
    {
        "name": "queue.status",
        "description": "Get global queue statistics",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": worker_skills.queue_status,
    },
    {
        "name": "task.status",
        "description": "Get details of a specific task",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": worker_skills.task_status,
    },
    {
        "name": "task.list_failed",
        "description": "List failed tasks in a batch",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": worker_skills.task_list_failed,
    },
    {
        "name": "worker.status",
        "description": "Get worker activity and queue summary",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": worker_skills.worker_status,
    },
    {
        "name": "comfyui.status",
        "description": "Check whether ComfyUI is online",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": comfyui_skills.comfyui_status,
    },
    {
        "name": "file.list_allowed",
        "description": "List files under an allowed root directory (metadata only)",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": file_skills.file_list_allowed,
    },
    {
        "name": "log.tail",
        "description": "Read the last N lines of gateway or worker logs",
        "danger_level": "low",
        "requires_confirmation": False,
        "implemented": True,
        "fn": log_skills.log_tail,
    },
    # ── Future skills (not yet implemented) ──────────────────────────────────
    {
        "name": "frame.extract",
        "description": "Extract frames from video files",
        "danger_level": "medium",
        "requires_confirmation": False,
        "implemented": False,
        "fn": future_skills.frame_extract,
    },
    {
        "name": "model3d.generate",
        "description": "Generate 3D model from input images",
        "danger_level": "high",
        "requires_confirmation": True,
        "implemented": False,
        "fn": future_skills.model3d_generate,
    },
    {
        "name": "texture.apply",
        "description": "Apply texture to a 3D asset",
        "danger_level": "medium",
        "requires_confirmation": False,
        "implemented": False,
        "fn": future_skills.texture_apply,
    },
    {
        "name": "workflow.run",
        "description": "Run an arbitrary ComfyUI workflow by name",
        "danger_level": "high",
        "requires_confirmation": True,
        "implemented": False,
        "fn": future_skills.workflow_run,
    },
]

_SKILL_MAP: dict[str, dict[str, Any]] = {s["name"]: s for s in SKILL_CATALOG}


# ── Manifest ──────────────────────────────────────────────────────────────────

def get_manifest() -> dict[str, Any]:
    from assetclaw_matting.config import settings
    return {
        "machine_id": settings.worker_id,
        "gpu": "RTX 3090 24GB",
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
        "comfyui_fake_mode": settings.comfyui_fake_mode,
        "available_skills": [
            {
                "name": s["name"],
                "description": s["description"],
                "danger_level": s["danger_level"],
                "requires_confirmation": s["requires_confirmation"],
                "implemented": s["implemented"],
            }
            for s in SKILL_CATALOG
        ],
    }


# ── Dispatcher ────────────────────────────────────────────────────────────────

def call_skill(
    name: str,
    arguments: dict[str, Any],
    requested_by: str = "api",
    request_id: str = "",
) -> dict[str, Any]:
    """Call a skill by name, log the call, return structured result."""
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
        log.info("Skill called: %s by=%s req=%s", name, requested_by, request_id)
        log_skill_call(request_id, name, arguments, result, True, None, requested_by)
        return {
            "ok": True,
            "skill": name,
            "result": result,
            "message": f"Skill {name} executed successfully",
        }
    except (ValueError, PermissionError) as exc:
        err = str(exc)
        log.warning("Skill %s failed: %s", name, err)
        log_skill_call(request_id, name, arguments, None, False, err, requested_by)
        return {"ok": False, "skill": name, "error": err, "message": err}
    except Exception as exc:
        err = str(exc)
        log.exception("Skill %s unexpected error", name)
        log_skill_call(request_id, name, arguments, None, False, err, requested_by)
        return {"ok": False, "skill": name, "error": err, "message": f"Internal error: {err}"}

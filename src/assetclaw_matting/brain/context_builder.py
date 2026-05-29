"""Build BrainContext from live machine state."""
from __future__ import annotations

from typing import Any

from assetclaw_matting.brain.schemas import BrainContext


_SECURITY_SUMMARY = (
    "SECURITY CONSTRAINTS: "
    "(1) No shell execution. "
    "(2) No file deletion. "
    "(3) All paths restricted to ALLOWED_ROOTS, blocked: .env .ssh Windows AppData. "
    "(4) No reading secrets or credentials. "
    "(5) All skill calls are logged. "
    "(6) GPU reserved for ComfyUI only — no local LLM."
)


def build_context() -> BrainContext:
    from assetclaw_matting.config import settings

    ctx = BrainContext(
        machine_id=settings.worker_id,
        gpu="RTX 3090 24GB",
        agent_runs_on_gpu=settings.agent_runs_on_gpu,
        allowed_roots=settings.allowed_roots_list or ["E:"],
        available_workflows=["matting_v1"],
        security_policy_summary=_SECURITY_SUMMARY,
    )

    ctx.queue_summary = _get_queue_summary()
    ctx.comfyui_status = _get_comfyui_status()
    ctx.worker_status = _get_worker_status()
    ctx.skills_manifest = _get_skills_manifest()
    ctx.recent_batches = _get_recent_batches()

    return ctx


def build_skills_prompt_block() -> str:
    """Return a concise skills description for LLM prompts."""
    from assetclaw_matting.skills.registry import SKILL_CATALOG
    lines = ["AVAILABLE SKILLS (call via skill name):"]
    for s in SKILL_CATALOG:
        if s["implemented"]:
            lines.append(
                f"  {s['name']}: {s['description']} "
                f"[danger:{s['danger_level']}]"
            )
    return "\n".join(lines)


def build_sop_summary() -> str:
    return (
        "BATCH MATTING SOP: "
        "(1) Ask user for input_dir and output_dir if not provided. "
        "(2) Use batch.create to create a batch. "
        "(3) Use batch.start to start it. "
        "(4) Monitor with batch.status / queue.status. "
        "(5) On failure: task.list_failed + log.tail. "
        "(6) Never delete original files. "
        "(7) Confirm before batch.cancel."
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_queue_summary() -> str:
    try:
        from assetclaw_matting.db import batch_repo, task_repo
        stats = task_repo.queue_stats()
        rb = batch_repo.running_batch_count()
        return (
            f"batches_running={rb} tasks_queued={stats['QUEUED']} "
            f"tasks_running={stats['RUNNING']} tasks_failed={stats['FAILED']}"
        )
    except Exception:
        return "unavailable"


def _get_comfyui_status() -> str:
    from assetclaw_matting.config import settings
    if settings.comfyui_fake_mode:
        return "fake_online"
    try:
        from assetclaw_matting.comfyui.client import comfyui_client
        comfyui_client.check_health()
        return "online"
    except Exception:
        return "offline"


def _get_worker_status() -> str:
    try:
        from assetclaw_matting.db.task_repo import queue_stats, list_tasks
        stats = queue_stats()
        running = list_tasks(status="RUNNING", limit=3)
        wids = list({t.worker_id for t in running if t.worker_id})
        return f"active_workers={wids} running_tasks={stats['RUNNING']}"
    except Exception:
        return "unavailable"


def _get_skills_manifest() -> dict[str, Any]:
    try:
        from assetclaw_matting.skills.registry import get_manifest
        return get_manifest()
    except Exception:
        return {}


def _get_recent_batches() -> list[dict[str, Any]]:
    try:
        from assetclaw_matting.db.batch_repo import list_batches
        return [b.model_dump() for b in list_batches(limit=5)]
    except Exception:
        return []

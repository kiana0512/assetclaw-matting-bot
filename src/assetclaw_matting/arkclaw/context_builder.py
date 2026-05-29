"""Build the machine context object sent to ArkClaw with every request.

This gives ArkClaw a live snapshot of this node's state so it can make
informed decisions about what to ask the node to do.
"""
from __future__ import annotations

from typing import Any


def build_machine_context() -> dict[str, Any]:
    """Return a live snapshot of this node's capabilities and state."""
    from assetclaw_matting.config import settings
    from assetclaw_matting.arkclaw.protocol import NODE_TYPE, NODE_VERSION, SECURITY_POLICY_SUMMARY

    ctx: dict[str, Any] = {
        "node_type": NODE_TYPE,
        "node_version": NODE_VERSION,
        "machine_id": settings.worker_id,
        "runtime": "windows",
        "gpu": "RTX 3090 24GB",
        "agent_runs_on_gpu": settings.agent_runs_on_gpu,
        "gpu_task_concurrency": settings.gpu_task_concurrency,
        "comfyui_url": settings.comfyui_url,
        "comfyui_fake_mode": settings.comfyui_fake_mode,
        "allowed_roots": settings.allowed_roots_list,
        "available_workflows": ["matting_v1"],
        "security_policy": SECURITY_POLICY_SUMMARY,
    }

    # Add live queue stats (best-effort — may fail if DB not initialised)
    ctx.update(_get_queue_snapshot())
    ctx["available_skills"] = _get_skill_names()

    return ctx


def build_queue_summary() -> str:
    """Return a human-readable one-liner queue summary."""
    try:
        from assetclaw_matting.db import batch_repo, task_repo
        stats = task_repo.queue_stats()
        running_batches = batch_repo.running_batch_count()
        return (
            f"Batches running: {running_batches}  |  "
            f"Tasks queued: {stats['QUEUED']}  "
            f"running: {stats['RUNNING']}  "
            f"failed: {stats['FAILED']}"
        )
    except Exception:
        return "Queue stats unavailable"


def _get_queue_snapshot() -> dict[str, Any]:
    try:
        from assetclaw_matting.db import batch_repo, task_repo
        stats = task_repo.queue_stats()
        return {
            "running_batches": batch_repo.running_batch_count(),
            "queued_tasks": stats["QUEUED"],
            "running_tasks": stats["RUNNING"],
            "failed_tasks": stats["FAILED"],
        }
    except Exception:
        return {}


def _get_skill_names() -> list[str]:
    try:
        from assetclaw_matting.skills.registry import SKILL_CATALOG
        return [s["name"] for s in SKILL_CATALOG if s["implemented"]]
    except Exception:
        return []

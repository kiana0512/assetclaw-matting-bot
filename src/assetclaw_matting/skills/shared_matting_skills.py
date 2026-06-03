from __future__ import annotations

from typing import Any

from assetclaw_matting.runtime_context import get_runtime_context


def shared_matting_start(
    shared_input_dir: str,
    shared_output_dir: str,
    workflow_path: str | None = None,
    notify_interval_seconds: int = 60,
    max_images: int = 500,
) -> dict[str, Any]:
    from assetclaw_matting.services.shared_matting_service import start_shared_matting_run

    ctx = get_runtime_context()
    return start_shared_matting_run(
        workflow_path=workflow_path,
        shared_input_dir=shared_input_dir,
        shared_output_dir=shared_output_dir,
        chat_id=str(ctx.get("chat_id") or ""),
        notify_interval_seconds=notify_interval_seconds,
        max_images=max_images,
    )


def shared_matting_status(run_id: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.services.shared_matting_service import shared_matting_status as get_status

    return get_status(run_id)


def shared_matting_sync_outputs(run_id: str) -> dict[str, Any]:
    from assetclaw_matting.services.shared_matting_service import sync_shared_outputs

    return sync_shared_outputs(run_id)

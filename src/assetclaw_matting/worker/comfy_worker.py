from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def run_matting(
    input_path: Path,
    output_image_path: Path,
    task_id: str = "",
) -> Path:
    """Run the matting workflow for one image.

    Delegates to ComfyUI client (real or fake based on COMFYUI_FAKE_MODE).
    Returns the path where the output was written.
    """
    from assetclaw_matting.comfyui.client import comfyui_client

    log.info(
        "Running matting: %s → %s (task=%s)",
        input_path.name, output_image_path.name, task_id or "?",
    )
    output_image_path.parent.mkdir(parents=True, exist_ok=True)
    return comfyui_client.run_workflow(
        input_image_path=input_path,
        output_image_path=output_image_path,
        task_id=task_id,
    )

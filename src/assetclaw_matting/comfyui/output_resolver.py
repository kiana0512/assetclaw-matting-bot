from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def resolve_first_output(
    history: dict[str, Any], prompt_id: str
) -> dict[str, str]:
    """Return the first image output dict (filename, subfolder, type) from history.

    Delegates to workflow_patch.find_save_image_outputs.
    """
    from assetclaw_matting.comfyui.workflow_patch import find_save_image_outputs

    images = find_save_image_outputs(history, prompt_id)
    first = images[0]
    log.debug("Resolved output: %s", first)
    return first

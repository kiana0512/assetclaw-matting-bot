from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def find_load_image_nodes(workflow: dict[str, Any]) -> list[str]:
    """Return node IDs of all LoadImage nodes."""
    return [
        node_id
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "LoadImage"
    ]


def patch_load_image(workflow: dict[str, Any], uploaded_filename: str) -> dict[str, Any]:
    """Replace the first LoadImage node's inputs.image with uploaded_filename.

    Mutates and returns the workflow dict.
    Raises ValueError if no LoadImage node is found.
    """
    node_ids = find_load_image_nodes(workflow)
    if not node_ids:
        raise ValueError(
            "No LoadImage node found in workflow. "
            "Make sure your ComfyUI workflow has a LoadImage node."
        )
    if len(node_ids) > 1:
        log.warning(
            "Multiple LoadImage nodes found (%s), patching only the first: %s",
            node_ids,
            node_ids[0],
        )
    target_id = node_ids[0]
    workflow[target_id].setdefault("inputs", {})["image"] = uploaded_filename
    log.debug("Patched LoadImage node %s → %s", target_id, uploaded_filename)
    return workflow


def find_save_image_outputs(
    history: dict[str, Any], prompt_id: str
) -> list[dict[str, Any]]:
    """Extract all image output entries from a ComfyUI history response.

    Returns a list of dicts with keys: filename, subfolder, type.
    Raises ValueError if nothing is found.
    """
    entry = history.get(prompt_id, {})
    outputs = entry.get("outputs", {})

    images: list[dict[str, Any]] = []
    for node_id, node_output in outputs.items():
        if "images" in node_output:
            for img in node_output["images"]:
                images.append(
                    {
                        "filename": img.get("filename", ""),
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                        "node_id": node_id,
                    }
                )

    if not images:
        raise ValueError(
            f"No SaveImage outputs found for prompt_id={prompt_id}. "
            "Make sure your workflow has a SaveImage node."
        )
    return images

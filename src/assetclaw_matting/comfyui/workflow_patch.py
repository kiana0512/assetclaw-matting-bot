from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def find_load_image_nodes(workflow: dict[str, Any]) -> list[str]:
    """Return node IDs of all image loader nodes in API or frontend workflows."""
    if _is_frontend_workflow(workflow):
        return [
            str(node.get("id"))
            for node in workflow.get("nodes", [])
            if isinstance(node, dict) and _looks_like_load_image_node(_frontend_node_info(node))
        ]
    return [
        node_id
        for node_id, node in workflow.items()
        if isinstance(node, dict) and _looks_like_load_image_node(_api_node_info(node_id, node))
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
    if _is_frontend_workflow(workflow):
        _patch_frontend_load_image(workflow, target_id, uploaded_filename)
    else:
        workflow[target_id].setdefault("inputs", {})["image"] = uploaded_filename
    log.debug("Patched LoadImage node %s → %s", target_id, uploaded_filename)
    return workflow


def patch_node_input(
    workflow: dict[str, Any],
    node_id: str,
    input_name: str,
    value: Any,
) -> dict[str, Any]:
    if node_id not in workflow:
        raise ValueError(f"node_id not found in workflow: {node_id}")
    workflow[node_id].setdefault("inputs", {})[input_name] = value
    return workflow


def patch_first_class_input(
    workflow: dict[str, Any],
    class_type: str,
    input_name: str,
    value: Any,
) -> dict[str, Any]:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return patch_node_input(workflow, node_id, input_name, value)
    raise ValueError(f"No {class_type} node found in workflow")


def workflow_to_api_prompt(workflow: dict[str, Any]) -> dict[str, Any]:
    if not _is_frontend_workflow(workflow):
        return workflow

    links = {
        int(link[0]): link
        for link in workflow.get("links", [])
        if isinstance(link, list) and len(link) >= 6
    }
    prompt: dict[str, Any] = {}
    for node in workflow.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id"))
        class_type = str(node.get("type") or "")
        if not node_id or not class_type:
            continue
        inputs: dict[str, Any] = {}
        widgets = list(node.get("widgets_values") or [])
        widget_index = 0
        for item in node.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            if name.lower() in {"upload"}:
                continue
            has_widget = item.get("widget") is not None
            widget_value = widgets[widget_index] if has_widget and widget_index < len(widgets) else None
            if has_widget:
                widget_index += 1
                if name.lower() in {"seed", "noise_seed"} and widget_index < len(widgets):
                    next_value = str(widgets[widget_index]).lower()
                    if next_value in {"fixed", "randomize", "increment", "decrement"}:
                        widget_index += 1
            link_id = item.get("link")
            if link_id is not None and int(link_id) in links:
                link = links[int(link_id)]
                inputs[name] = [str(link[1]), int(link[2])]
                continue
            if has_widget:
                inputs[name] = widget_value
        prompt[node_id] = {"class_type": class_type, "inputs": inputs}
    return prompt


def prepare_api_prompt_for_run(workflow: dict[str, Any]) -> dict[str, Any]:
    """Convert a workflow to the ComfyUI API prompt format without changing it."""
    return workflow_to_api_prompt(workflow)


def inspect_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    class_counts: dict[str, int] = {}
    if _is_frontend_workflow(workflow):
        for node in workflow.get("nodes", []):
            if not isinstance(node, dict):
                continue
            item = _frontend_node_info(node)
            class_counts[item["class_type"]] = class_counts.get(item["class_type"], 0) + 1
            nodes.append(item)
    else:
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            item = _api_node_info(node_id, node)
            class_counts[item["class_type"]] = class_counts.get(item["class_type"], 0) + 1
            nodes.append(item)
    return {
        "node_count": len(nodes),
        "class_counts": class_counts,
        "load_image_nodes": [item for item in nodes if _looks_like_load_image_node(item)],
        "save_image_nodes": [item for item in nodes if _looks_like_save_image_node(item)],
        "nodes": nodes[:100],
    }


def find_save_image_node_ids(workflow: dict[str, Any]) -> list[str]:
    """Return node IDs that are actual SaveImage/保存图像 nodes."""
    return [str(item["id"]) for item in inspect_workflow(workflow).get("save_image_nodes") or [] if item.get("id")]


def find_primary_save_image_node_id(workflow: dict[str, Any]) -> str:
    """Return the intended final SaveImage node, excluding preview/mask saves.

    Animation matting workflows may contain extra preview/mask image nodes after
    the real output. The production output is the node explicitly titled
    保存图像 / Save Image.
    """
    candidates = inspect_workflow(workflow).get("save_image_nodes") or []
    clean = [item for item in candidates if not _looks_like_non_final_output_node(item)]
    if clean:
        ranked = sorted(
            clean,
            key=lambda item: _primary_save_score(workflow, item),
            reverse=True,
        )
        return str(ranked[0]["id"])
    if candidates:
        return str(candidates[0]["id"])
    return ""


def _is_frontend_workflow(workflow: dict[str, Any]) -> bool:
    return isinstance(workflow.get("nodes"), list)


def _api_node_info(node_id: str, node: dict[str, Any]) -> dict[str, Any]:
    class_type = str(node.get("class_type") or node.get("type") or "unknown")
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    return {
        "id": str(node_id),
        "class_type": class_type,
        "title": str(node.get("_meta", {}).get("title") or node.get("title") or ""),
        "inputs": sorted(str(key) for key in inputs.keys()),
        "input_labels": [],
        "outputs": [],
        "output_labels": [],
        "widgets": [],
    }


def _frontend_node_info(node: dict[str, Any]) -> dict[str, Any]:
    class_type = str(node.get("type") or node.get("class_type") or "unknown")
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    inputs = node.get("inputs") if isinstance(node.get("inputs"), list) else []
    outputs = node.get("outputs") if isinstance(node.get("outputs"), list) else []
    widgets = node.get("widgets_values") if isinstance(node.get("widgets_values"), list) else []
    return {
        "id": str(node.get("id")),
        "class_type": class_type,
        "title": str(node.get("title") or props.get("title") or props.get("Node name for S&R") or ""),
        "inputs": [str(item.get("name") or "") for item in inputs if isinstance(item, dict)],
        "input_labels": [
            str(item.get("localized_name") or item.get("label") or item.get("name") or "")
            for item in inputs
            if isinstance(item, dict)
        ],
        "outputs": [str(item.get("name") or "") for item in outputs if isinstance(item, dict)],
        "output_labels": [
            str(item.get("localized_name") or item.get("label") or item.get("name") or "")
            for item in outputs
            if isinstance(item, dict)
        ],
        "widgets": [str(item) for item in widgets],
    }


def _looks_like_load_image_node(item: dict[str, Any]) -> bool:
    text = _node_search_text(item)
    return "loadimage" in text or "load image" in text or "加载图像" in text or "加载图片" in text


def _looks_like_save_image_node(item: dict[str, Any]) -> bool:
    text = _node_search_text(item)
    return "saveimage" in text or "save image" in text or "保存图像" in text or "保存图片" in text


def _node_search_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("class_type", ""),
        item.get("title", ""),
        " ".join(item.get("inputs") or []),
        " ".join(item.get("input_labels") or []),
        " ".join(item.get("outputs") or []),
        " ".join(item.get("output_labels") or []),
        " ".join(item.get("widgets") or []),
    ]
    return " ".join(str(part) for part in parts).lower()


def _patch_frontend_load_image(workflow: dict[str, Any], node_id: str, uploaded_filename: str) -> None:
    for node in workflow.get("nodes", []):
        if not isinstance(node, dict) or str(node.get("id")) != str(node_id):
            continue
        widgets = node.setdefault("widgets_values", [])
        if widgets:
            widgets[0] = uploaded_filename
        else:
            widgets.append(uploaded_filename)
        return
    raise ValueError(f"node_id not found in workflow: {node_id}")


def find_save_image_outputs(
    history: dict[str, Any],
    prompt_id: str,
    final_save_image_node_id: str | None = None,
) -> list[dict[str, Any]]:
    """Extract image entries from the final SaveImage node in a ComfyUI history response.

    Returns a list of dicts with keys: filename, subfolder, type.
    Raises ValueError if nothing is found.
    """
    entry = history.get(prompt_id, {})
    outputs = entry.get("outputs", {})
    final_node_id = str(final_save_image_node_id or "").strip()
    if not final_node_id:
        final_node_id = _infer_primary_save_image_node_id_from_history_entry(entry)

    images: list[dict[str, Any]] = []
    for node_id, node_output in outputs.items():
        if final_node_id and str(node_id) != final_node_id:
            continue
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
        suffix = f" Expected final SaveImage node id: {final_node_id}." if final_node_id else ""
        raise ValueError(
            f"No final SaveImage outputs found for prompt_id={prompt_id}.{suffix} "
            "Make sure the workflow's 保存图像/SaveImage node is connected and completed."
        )
    output_images = [item for item in images if item.get("type") == "output"]
    return output_images or images


def _infer_primary_save_image_node_id_from_history_entry(entry: dict[str, Any]) -> str:
    prompt = entry.get("prompt")
    prompt_graph: dict[str, Any] | None = None
    if isinstance(prompt, dict):
        prompt_graph = prompt
    elif isinstance(prompt, list):
        for item in prompt:
            if isinstance(item, dict) and item:
                prompt_graph = item
                break
    if not isinstance(prompt_graph, dict):
        return ""
    return find_primary_save_image_node_id(prompt_graph)


def _looks_like_non_final_output_node(item: dict[str, Any]) -> bool:
    text = _node_search_text(item)
    return any(token in text for token in ("preview", "预览", "mask", "遮罩", "蒙版", "无校色"))


def _looks_like_primary_save_title(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "").strip().lower()
    class_type = str(item.get("class_type") or "").strip().lower()
    if title in {"保存图像", "保存图片", "save image", "saveimage"}:
        return True
    return not title and class_type in {"saveimage", "save image"}


def _primary_save_score(workflow: dict[str, Any], item: dict[str, Any]) -> tuple[int, int, int]:
    source = _save_image_source_info(workflow, str(item.get("id") or ""))
    source_text = " ".join(str(part) for part in source.values()).lower()
    score = 0
    if "cherryitemcolorrestore" in source_text:
        score += 100
    if "rgba" in source_text or "图像(rgba)" in source_text:
        score += 50
    if _looks_like_primary_save_title(item):
        score += 20
    # Prefer earlier clean SaveImage nodes over late preview/debug nodes when
    # scores tie. Dict/list order follows the workflow file order.
    order = -_workflow_node_order(workflow, str(item.get("id") or ""))
    return (score, order, -int(str(item.get("id") or "0")) if str(item.get("id") or "0").isdigit() else 0)


def _save_image_source_info(workflow: dict[str, Any], node_id: str) -> dict[str, str]:
    if _is_frontend_workflow(workflow):
        nodes = {str(node.get("id")): node for node in workflow.get("nodes", []) if isinstance(node, dict)}
        node = nodes.get(str(node_id)) or {}
        link_id = None
        for item in node.get("inputs") or []:
            if isinstance(item, dict) and str(item.get("name") or "").lower() in {"images", "image", "图像", "图片"}:
                link_id = item.get("link")
                break
        if link_id is None:
            return {}
        for link in workflow.get("links") or []:
            if not isinstance(link, list) or len(link) < 6 or str(link[0]) != str(link_id):
                continue
            source_id = str(link[1])
            source_slot = int(link[2] or 0)
            source_node = nodes.get(source_id) or {}
            outputs = source_node.get("outputs") if isinstance(source_node.get("outputs"), list) else []
            output = outputs[source_slot] if 0 <= source_slot < len(outputs) and isinstance(outputs[source_slot], dict) else {}
            return {
                "source_id": source_id,
                "source_type": str(source_node.get("type") or source_node.get("class_type") or ""),
                "source_title": str(source_node.get("title") or ""),
                "source_output": str(output.get("name") or output.get("localized_name") or output.get("label") or ""),
            }
        return {}

    node = workflow.get(str(node_id)) or {}
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    image_input = inputs.get("images") or inputs.get("image") or inputs.get("图像") or inputs.get("图片")
    if not (isinstance(image_input, list) and image_input):
        return {}
    source_id = str(image_input[0])
    source_node = workflow.get(source_id) or {}
    return {
        "source_id": source_id,
        "source_type": str(source_node.get("class_type") or source_node.get("type") or ""),
        "source_title": str(source_node.get("_meta", {}).get("title") or source_node.get("title") or ""),
        "source_output": "",
    }


def _workflow_node_order(workflow: dict[str, Any], node_id: str) -> int:
    if _is_frontend_workflow(workflow):
        for index, node in enumerate(workflow.get("nodes") or []):
            if isinstance(node, dict) and str(node.get("id")) == str(node_id):
                return index
        return 999999
    for index, key in enumerate(workflow.keys()):
        if str(key) == str(node_id):
            return index
    return 999999

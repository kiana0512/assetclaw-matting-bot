from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)


def resolve_first_output(
    history: dict[str, Any],
    prompt_id: str,
    *,
    final_save_image_node_id: str | None = None,
) -> dict[str, str]:
    """Return the first image output dict (filename, subfolder, type) from history.

    Delegates to workflow_patch.find_save_image_outputs.
    """
    from assetclaw_matting.comfyui.workflow_patch import find_save_image_outputs

    images = find_save_image_outputs(history, prompt_id, final_save_image_node_id=final_save_image_node_id)
    first = images[0]
    log.debug("Resolved output: %s", first)
    return first


def resolve_best_output(
    history: dict[str, Any],
    prompt_id: str,
    *,
    local_path_resolver=None,
    final_save_image_node_id: str | None = None,
) -> dict[str, str]:
    """Choose the best saved PNG output for matting.

    Some animation workflows save multiple images per prompt: white-background
    preview, mask, black-background preview, and the final transparent PNG.
    We must not re-save or composite images here. When the local ComfyUI output
    file is available, inspect only metadata/pixels to select the PNG that
    already contains a meaningful alpha channel, then copy that original file.
    """
    from assetclaw_matting.comfyui.workflow_patch import find_save_image_outputs

    images = find_save_image_outputs(history, prompt_id, final_save_image_node_id=final_save_image_node_id)
    if not local_path_resolver:
        return images[0]

    scored: list[tuple[tuple[int, int, int, int], dict[str, str]]] = []
    diagnostics: list[str] = []
    for index, item in enumerate(images):
        path = local_path_resolver(item.get("filename", ""), item.get("subfolder", ""), item.get("type", "output"))
        quality = inspect_local_png(Path(path) if path else None)
        score = _score_output_quality(quality, index)
        scored.append((score, item))
        diagnostics.append(_format_candidate_diagnostic(index, item, quality))
        log.debug("ComfyUI output candidate score=%s item=%s path=%s", score, item, path)

    scored.sort(reverse=True, key=lambda pair: pair[0])
    if not scored or scored[0][0][0] <= 0:
        raise ValueError(
            "ComfyUI 没有找到合格的最终透明 PNG 输出，已拒绝同步中间图。候选："
            + " | ".join(diagnostics[:12])
        )
    selected = scored[0][1]
    log.debug("Resolved best output: %s", selected)
    return selected


def inspect_local_png(path: Path | None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path) if path else "",
        "exists": bool(path and path.exists()),
        "valid": False,
        "reason": "missing",
        "width": 0,
        "height": 0,
        "mode": "",
        "has_alpha": False,
        "alpha_min": None,
        "alpha_max": None,
        "transparent_pixels": 0,
        "opaque_pixels": 0,
        "file_size": 0,
    }
    if not path or not path.exists():
        return info
    try:
        info["file_size"] = int(path.stat().st_size)
        with Image.open(path) as image:
            info["width"], info["height"] = image.size
            info["mode"] = image.mode
            has_alpha = "A" in image.getbands()
            info["has_alpha"] = has_alpha
            if has_alpha:
                alpha = image.getchannel("A")
                lo, hi = alpha.getextrema()
                hist = alpha.histogram()
                info["alpha_min"] = int(lo)
                info["alpha_max"] = int(hi)
                info["transparent_pixels"] = int(sum(hist[:255]))
                info["opaque_pixels"] = int(hist[255])
                if info["transparent_pixels"] > 0:
                    mask_like = _looks_like_mask_image(image)
                    info["mask_like"] = mask_like
                    if mask_like:
                        info["reason"] = "mask_like_intermediate"
                    else:
                        info["valid"] = True
                        info["reason"] = "transparent_alpha"
                else:
                    info["reason"] = "alpha_is_fully_opaque"
            else:
                info["reason"] = "no_alpha_channel"
    except (OSError, UnidentifiedImageError, ValueError):
        info["reason"] = "unreadable_png"
    return info


def _looks_like_mask_image(image: Image.Image) -> bool:
    rgba = image.convert("RGBA")
    rgba.thumbnail((128, 128))
    visible = []
    for r, g, b, a in rgba.getdata():
        if a > 16:
            visible.append((r, g, b))
    if len(visible) < 32:
        return True
    sample = visible[:: max(1, len(visible) // 4096)]
    chroma_pixels = 0
    varied_pixels = 0
    for r, g, b in sample:
        spread = max(r, g, b) - min(r, g, b)
        if spread > 18:
            chroma_pixels += 1
        if not (r > 235 and g > 235 and b > 235) and not (r < 20 and g < 20 and b < 20):
            varied_pixels += 1
    chroma_ratio = chroma_pixels / max(1, len(sample))
    varied_ratio = varied_pixels / max(1, len(sample))
    return chroma_ratio < 0.02 and varied_ratio < 0.08


def _score_output_quality(quality: dict[str, Any], index: int) -> tuple[int, int, int, int]:
    if not quality.get("valid"):
        return (0, 0, index, int(quality.get("file_size") or 0))
    alpha_min = int(quality.get("alpha_min") or 0)
    alpha_max = int(quality.get("alpha_max") or 0)
    alpha_span = alpha_max - alpha_min
    # Prefer real transparent outputs, and when several are valid choose the
    # later SaveImage/history candidate because the workflow saves previews first.
    return (2, alpha_span, index, int(quality.get("file_size") or 0))


def _format_candidate_diagnostic(index: int, item: dict[str, str], quality: dict[str, Any]) -> str:
    filename = item.get("filename", "")
    node_id = item.get("node_id", "")
    size = f"{quality.get('width', 0)}x{quality.get('height', 0)}"
    alpha = f"{quality.get('alpha_min')}..{quality.get('alpha_max')}" if quality.get("has_alpha") else "none"
    return (
        f"#{index} node={node_id} file={filename} size={size} mode={quality.get('mode') or '?'} "
        f"alpha={alpha} reason={quality.get('reason')}"
    )

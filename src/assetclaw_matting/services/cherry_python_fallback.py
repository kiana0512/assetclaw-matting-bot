from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True)
class CherryPythonResult:
    output_dir: Path
    total: int
    profile: str
    resize: str
    feather_enabled: bool
    steps: list[str]


def run_cherry_python_fallback(input_root: Path, output_root: Path, files: list[Path]) -> CherryPythonResult:
    if not files:
        raise ValueError("no input images")
    first = Image.open(files[0])
    profile = "half" if first.width == first.height else "full"
    size = (256, 256) if profile == "half" else (384, 512)
    feather_enabled = profile != "half"
    steps = ["fringe", "hairinset", *([] if profile == "half" else ["feather"]), "blur", "resize2"]
    output_root.mkdir(parents=True, exist_ok=True)
    for path in files:
        image = Image.open(path).convert("RGBA")
        image = _clean_fringe(image)
        image = _hair_inset(image)
        if feather_enabled:
            image = _alpha_feather(image)
        image = _blur_under_composite(image)
        image = _resize_fit(image, size)
        target = output_root / path.relative_to(input_root).with_suffix(".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
    return CherryPythonResult(
        output_dir=output_root,
        total=len(files),
        profile=profile,
        resize=f"{size[0]}x{size[1]}",
        feather_enabled=feather_enabled,
        steps=steps,
    )


def _clean_fringe(image: Image.Image) -> Image.Image:
    arr = np.array(image).astype(np.float32)
    alpha = arr[..., 3] / 255.0
    edge = _edge_band(alpha, radius=4)
    if not np.any(edge):
        return image
    interior = _blur_rgb(arr[..., :3], radius=4)
    weight = np.clip(edge[..., None] * (1.0 - alpha[..., None] * 0.65), 0.0, 1.0)
    arr[..., :3] = arr[..., :3] * (1.0 - weight) + interior * weight
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def _hair_inset(image: Image.Image) -> Image.Image:
    arr = np.array(image)
    alpha = Image.fromarray(arr[..., 3], "L")
    hair_line = _upper_body_line(arr[..., 3], ratio=0.62)
    top = alpha.crop((0, 0, alpha.width, max(1, hair_line + 1)))
    top = top.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.0))
    merged = alpha.copy()
    merged.paste(top, (0, 0))
    arr[..., 3] = np.array(merged)
    arr[arr[..., 3] < 8] = 0
    return Image.fromarray(arr, "RGBA")


def _alpha_feather(image: Image.Image) -> Image.Image:
    arr = np.array(image).astype(np.float32)
    a = arr[..., 3] / 255.0
    edge0, edge1 = 0.018, 0.983
    t = np.clip((a - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    arr[..., 3] = (t * t * (3.0 - 2.0 * t) * 255.0)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def _blur_under_composite(image: Image.Image) -> Image.Image:
    arr = np.array(image).astype(np.float32)
    alpha = arr[..., 3:4] / 255.0
    blurred = np.array(image.filter(ImageFilter.GaussianBlur(12))).astype(np.float32)
    under = blurred[..., :3]
    ring = _edge_band(alpha[..., 0], radius=14)[..., None]
    rgb = arr[..., :3] * alpha + under * (1.0 - alpha) * ring
    arr[..., :3] = np.where(alpha > 0, np.clip(rgb / np.maximum(alpha + (1.0 - alpha) * ring, 1e-6), 0, 255), arr[..., :3])
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def _resize_fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    work = image.copy()
    work.thumbnail(size, Image.Resampling.LANCZOS)
    x = (size[0] - work.width) // 2
    y = (size[1] - work.height) // 2
    canvas.alpha_composite(work, (x, y))
    return canvas


def _edge_band(alpha: np.ndarray, radius: int) -> np.ndarray:
    mask = Image.fromarray(np.clip(alpha * 255, 0, 255).astype(np.uint8), "L")
    dilated = mask.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    eroded = mask.filter(ImageFilter.MinFilter(radius * 2 + 1))
    return np.clip((np.array(dilated).astype(np.float32) - np.array(eroded).astype(np.float32)) / 255.0, 0.0, 1.0)


def _blur_rgb(rgb: np.ndarray, radius: float) -> np.ndarray:
    image = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    return np.array(image.filter(ImageFilter.GaussianBlur(radius))).astype(np.float32)


def _upper_body_line(alpha: np.ndarray, ratio: float) -> int:
    ys = np.where(alpha >= 24)[0]
    if ys.size == 0:
        return 0
    top = int(ys.min())
    bottom = int(ys.max())
    return top + round((bottom - top + 1) * ratio)

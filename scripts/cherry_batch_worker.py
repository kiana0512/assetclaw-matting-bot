from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import types
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

_AUTO_PROFILE_TOLERANCE = 0.01
_PROFILE_OVERRIDE_KEYS = {
    "use_denoise",
    "denoise_threshold",
    "denoise_radius",
    "use_shadow",
    "use_blur",
    "blur_radius",
    "blur_sigma",
    "use_resize1",
    "resize1_width",
    "resize1_height",
    "use_sharp1",
    "sharp1_amount",
    "sharp1_radius",
    "sharp1_threshold",
    "sharp1_shrink",
    "use_resize2",
    "resize2_width",
    "resize2_height",
    "use_sharp2",
    "sharp2_amount",
    "sharp2_radius",
    "sharp2_threshold",
    "sharp2_shrink",
    "use_resize",
    "resize_width",
    "resize_height",
    "use_sharpen",
    "sharpen_amount",
    "sharpen_radius",
    "sharpen_threshold",
    "sharpen_shrink",
}


def main() -> int:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    source = Path(config["source_path"])
    src = Path(config["input_dir"])
    dst = Path(config["output_dir"])
    files = [Path(path) for path in config["files"]]
    options = config["options"]
    module = _load_module(source)
    groups = _group_sequences(src, files)
    completed = 0
    failed = 0
    for group_files in groups:
        for process_files in _compatible_batches(module, group_files, bool(options.get("use_smooth"))):
            try:
                batch_np = [module.decode(path.read_bytes()) for path in process_files]
                batch = torch.from_numpy(np.stack(batch_np)).float() / 255.0
                batch_options = _options_for_batch_shape(options, int(batch.shape[1]), int(batch.shape[2]))
                batch = _apply_cherry_pipeline(module, batch, batch_options)
                out_np = (batch.detach().cpu().numpy().clip(0, 1) * 255).astype(np.uint8)
                for index, image_path in enumerate(process_files):
                    target = dst / image_path.relative_to(src).with_suffix(".png")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(module.encode(out_np[index]))
                    completed += 1
                    _emit({"event": "done", "src_path": str(image_path), "dst_path": str(target), "rel_path": str(image_path.relative_to(src)), "completed": completed, "failed": failed})
            except Exception as exc:
                for image_path in process_files:
                    failed += 1
                    _emit({"event": "error", "src_path": str(image_path), "rel_path": str(image_path.relative_to(src)), "error": str(exc), "completed": completed, "failed": failed})
                return 2
    _emit({"event": "finished", "completed": completed, "failed": failed})
    return 0


def _load_module(source: Path):
    _install_flask_stub()
    spec = importlib.util.spec_from_file_location("assetclaw_cherry_temporal_smooth_worker", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Cherry tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_flask_stub() -> None:
    if "flask" in sys.modules:
        return

    class _DummyApp:
        def __init__(self, *_args, **_kwargs):
            self.config = {}

        def route(self, *_args, **_kwargs):
            def decorator(fn):
                return fn

            return decorator

        def run(self, *_args, **_kwargs):
            return None

    flask = types.ModuleType("flask")
    flask.Flask = _DummyApp
    flask.jsonify = lambda *args, **kwargs: {"args": args, **kwargs}
    flask.request = types.SimpleNamespace(form={}, files={})
    flask.send_file = lambda *args, **kwargs: None
    flask.render_template_string = lambda *args, **kwargs: ""
    sys.modules["flask"] = flask


def _group_sequences(root: Path, files: list[Path]) -> list[list[Path]]:
    groups: dict[Path, list[Path]] = defaultdict(list)
    for path in files:
        groups[path.parent.relative_to(root)].append(path)
    return [sorted(paths, key=lambda path: path.name.lower()) for _rel, paths in sorted(groups.items(), key=lambda item: str(item[0]).lower())]


def _compatible_batches(module, files: list[Path], require_same_shape: bool) -> list[list[Path]]:
    by_shape: dict[tuple[int, ...], list[Path]] = defaultdict(list)
    for path in files:
        shape = tuple(module.decode(path.read_bytes()).shape)
        by_shape[shape].append(path)
    if len(by_shape) <= 1:
        return [files]
    if require_same_shape:
        raise ValueError(f"同一序列内图片尺寸不一致：{files[0].parent}")
    return [paths for _shape, paths in sorted(by_shape.items(), key=lambda item: str(item[0]))]


def _temporal_smooth(module, batch, options):
    args = [
        batch,
        int(options.get("smooth_window", 5)),
        float(options.get("smooth_sigma", 1.0)),
        bool(options.get("sync_rgb", False)),
        float(options.get("min_alpha", 0.05)),
    ]
    parameter_count = len(inspect.signature(module.temporal_smooth).parameters)
    if parameter_count >= 6:
        args.append(int(options.get("ring_width", 25)))
    if parameter_count >= 7:
        args.append(str(options.get("smooth_method", "中值+高斯")))
    if parameter_count >= 8:
        args.append(bool(options.get("fill_gap", True)))
    if parameter_count >= 9:
        args.append(float(options.get("bg_thresh", 0.02)))
    return module.temporal_smooth(*args)


def _apply_cherry_pipeline(module, batch, options):
    shadow_source = batch
    if options.get("use_denoise"):
        batch = module.alpha_denoise(
            batch,
            float(options.get("denoise_threshold", 0.06)),
            int(options.get("denoise_radius", 0)),
        )
    if options.get("use_shadow"):
        batch = _shadow_separate_char(module, batch, shadow_source, options)
    if options.get("use_blur") and hasattr(module, "blur_under_composite"):
        batch = module.blur_under_composite(batch, int(options.get("blur_radius", 1)), float(options.get("blur_sigma", 10.0)))
    if options.get("use_resize1"):
        batch = module.ps_bicubic_sharper(batch, int(options.get("resize1_width", 768)), int(options.get("resize1_height", 1024)))
    if options.get("use_sharp1"):
        batch = _sharpen(
            module,
            batch,
            float(options.get("sharp1_amount", 1.0)),
            int(options.get("sharp1_radius", 2)),
            float(options.get("sharp1_threshold", 0.02)),
            int(options.get("sharp1_shrink", 0)),
            float(options.get("min_alpha", 0.05)),
        )
    if options.get("use_resize2"):
        batch = module.ps_bicubic_sharper(batch, int(options.get("resize2_width", 384)), int(options.get("resize2_height", 512)))
    if options.get("use_sharp2"):
        batch = _sharpen(
            module,
            batch,
            float(options.get("sharp2_amount", 1.0)),
            int(options.get("sharp2_radius", 2)),
            float(options.get("sharp2_threshold", 0.02)),
            int(options.get("sharp2_shrink", 5)),
            float(options.get("min_alpha", 0.05)),
        )
    if options.get("use_smooth"):
        batch = _temporal_smooth(module, batch, options)
    return batch


def _infer_profile_from_shape(height: int, width: int) -> str:
    if height <= 0 or width <= 0:
        return "full"
    ratio = float(width) / float(height)
    return "half" if abs(ratio - 1.0) <= _AUTO_PROFILE_TOLERANCE else "full"


def _preset_overrides(profile: str, use_smooth: bool) -> dict:
    is_half = profile == "half"
    width, height = (256, 256) if is_half else (384, 512)
    return {
        "use_denoise": True,
        "denoise_threshold": 0.10 if is_half else 0.85,
        "denoise_radius": 0,
        "use_shadow": not is_half,
        "use_blur": True,
        "blur_radius": 1,
        "blur_sigma": 10.0,
        "use_resize1": True,
        "resize1_width": width,
        "resize1_height": height,
        "use_sharp1": True,
        "sharp1_amount": 1.0,
        "sharp1_radius": 2,
        "sharp1_threshold": 0.02,
        "sharp1_shrink": 0,
        "use_resize2": not is_half,
        "resize2_width": width,
        "resize2_height": height,
        "use_sharp2": not is_half,
        "sharp2_amount": 1.0,
        "sharp2_radius": 2,
        "sharp2_threshold": 0.02,
        "sharp2_shrink": 5,
        "use_smooth": bool(use_smooth),
        "use_resize": True,
        "resize_width": width,
        "resize_height": height,
        "use_sharpen": True,
        "sharpen_amount": 1.0,
        "sharpen_radius": 2,
        "sharpen_threshold": 0.02,
        "sharpen_shrink": 5,
    }


def _options_for_batch_shape(options: dict, height: int, width: int) -> dict:
    if not options.get("auto_profile_by_size"):
        return options
    inferred = _infer_profile_from_shape(height, width)
    preset = _preset_overrides(inferred, bool(options.get("use_smooth", False)))
    adjusted = dict(options)
    for key in _PROFILE_OVERRIDE_KEYS:
        adjusted[key] = preset[key]
    adjusted["profile"] = "auto"
    adjusted["inferred_profile"] = inferred
    adjusted["auto_profile_by_size"] = True
    return adjusted


def _shadow_separate_char(module, batch, shadow_source, options):
    separator = getattr(module, "shadow_separate_v5", None) or getattr(module, "shadow_separate", None)
    if separator is None:
        shadow_clean = module.alpha_denoise(shadow_source, 0.01, 0)
        return _merge_item_shadow(batch, shadow_clean, options)

    branch = module.alpha_denoise(shadow_source.clone(), 0.01, 0)
    item_branch = module.alpha_denoise(
        shadow_source.clone(),
        float(options.get("denoise_threshold", 0.06)),
        int(options.get("denoise_radius", 0)),
    )
    if item_branch.shape[1:3] != branch.shape[1:3]:
        item_branch = module.ps_bicubic_sharper(item_branch, branch.shape[2], branch.shape[1])
    item_a = item_branch[..., 3:4].clamp(0.0, 1.0)
    item_rgb_ref = (item_branch[..., :3] * item_a + (1.0 - item_a)).clamp(0.0, 1.0)
    char_branch, shadow_batch = separator(
        branch,
        float(options.get("shadow_gray_limit", 0.35)),
        int(options.get("shadow_protect_radius", -70)),
        0.1,
        float(options.get("shadow_alpha_boost", 1.0)),
        int(options.get("shadow_blur_radius", 2)),
        float(options.get("shadow_blur_sigma", 2.4)),
        item_alpha=item_branch[..., 3],
        item_rgb=item_rgb_ref,
    )
    if char_branch.shape[1:3] != batch.shape[1:3]:
        char_branch = module.ps_bicubic_sharper(char_branch, batch.shape[2], batch.shape[1])
    if shadow_batch.shape[1:3] != batch.shape[1:3]:
        shadow_batch = module.ps_bicubic_sharper(shadow_batch, batch.shape[2], batch.shape[1])
    if item_branch.shape[1:3] != shadow_batch.shape[1:3]:
        item_branch = module.ps_bicubic_sharper(item_branch, shadow_batch.shape[2], shadow_batch.shape[1])

    solid_now = (item_branch[..., 3] > 0.01).cpu().numpy().astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    solid_now = np.stack([module.cv2.dilate(solid_now[i], kernel) for i in range(solid_now.shape[0])], axis=0)
    protect = torch.from_numpy(solid_now.astype(np.float32)).to(batch.device).unsqueeze(-1)
    new_a = torch.where(
        protect > 0.5,
        batch[..., 3:4],
        torch.minimum(
            torch.clamp(batch[..., 3:4] - shadow_batch[..., 3:4], 0.0, 1.0),
            char_branch[..., 3:4].clamp(0.0, 1.0),
        ),
    )
    return torch.cat([batch[..., :3], new_a], dim=-1)


def _sharpen(module, batch, amount, radius, threshold, shrink, min_alpha):
    args = [batch, amount, radius, threshold, shrink]
    if len(inspect.signature(module.sharpen).parameters) >= 6:
        args.append(min_alpha)
    return module.sharpen(*args)


def _merge_item_shadow(batch, shadow_source, options):
    if getattr(batch, "shape", None) is None or batch.shape[-1] < 4:
        return batch
    gray_limit = float(options.get("shadow_gray_limit", 0.35))
    protect_radius = int(options.get("shadow_protect_radius", -70))
    boost = float(options.get("shadow_alpha_boost", 1.0))

    src_rgb = shadow_source[..., :3].clamp(0.0, 1.0)
    src_a = shadow_source[..., 3:4].clamp(0.0, 1.0)
    gray = src_rgb.mean(dim=-1, keepdim=True)
    chroma = src_rgb.max(dim=-1, keepdim=True).values - src_rgb.min(dim=-1, keepdim=True).values

    _, h, w, _ = batch.shape
    yy = torch.linspace(0.0, 1.0, h, device=batch.device, dtype=batch.dtype).view(1, h, 1, 1)
    xx = torch.linspace(0.0, 1.0, w, device=batch.device, dtype=batch.dtype).view(1, 1, w, 1)
    foot_ellipse = (((xx - 0.5) / 0.46) ** 2 + ((yy - 0.82) / 0.24) ** 2) <= 1.0
    shadow_mask = (src_a > 0.01) & (gray <= gray_limit) & (chroma <= 0.12) & foot_ellipse

    if protect_radius:
        person = (src_a > float(options.get("min_alpha", 0.05))).permute(0, 3, 1, 2).float()
        radius = min(abs(protect_radius), max(1, min(h, w) // 3))
        kernel = radius * 2 + 1
        if protect_radius < 0:
            protected = -torch.nn.functional.max_pool2d(-person, kernel, stride=1, padding=radius)
        else:
            protected = torch.nn.functional.max_pool2d(person, kernel, stride=1, padding=radius)
        shadow_mask = shadow_mask & ~(protected.permute(0, 2, 3, 1) > 0.5)

    base_rgb = batch[..., :3].clamp(0.0, 1.0)
    base_a = batch[..., 3:4].clamp(0.0, 1.0)
    shadow_a = torch.where(shadow_mask, (src_a * boost).clamp(0.0, 1.0), torch.zeros_like(src_a))
    shadow_rgb = src_rgb * 0.75
    out_a = torch.maximum(base_a, shadow_a)
    out_rgb = torch.where(
        out_a > 1e-6,
        (base_rgb * base_a + shadow_rgb * shadow_a * (1.0 - base_a)).clamp(0.0, 1.0) / out_a.clamp(min=1e-6),
        base_rgb,
    )
    return torch.cat([out_rgb, out_a], dim=-1)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

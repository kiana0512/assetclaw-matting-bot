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
                if options.get("use_denoise"):
                    batch = module.alpha_denoise(
                        batch,
                        float(options.get("denoise_threshold", 0.06)),
                        int(options.get("denoise_radius", 0)),
                    )
                if options.get("use_smooth"):
                    batch = _temporal_smooth(module, batch, options)
                if options.get("use_resize"):
                    batch = module.ps_bicubic_sharper(batch, int(options.get("resize_width", 256)), int(options.get("resize_height", 256)))
                if options.get("use_sharpen"):
                    batch = module.sharpen(
                        batch,
                        float(options.get("sharpen_amount", 2.0)),
                        int(options.get("sharpen_radius", 2)),
                        float(options.get("sharpen_threshold", 0.02)),
                        int(options.get("sharpen_shrink", 4)),
                        float(options.get("min_alpha", 0.05)),
                    )
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
        bool(options.get("sync_rgb", True)),
        float(options.get("min_alpha", 0.05)),
    ]
    if len(inspect.signature(module.temporal_smooth).parameters) >= 6:
        args.append(int(options.get("ring_width", 25)))
    return module.temporal_smooth(*args)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _run_label() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _collect_images(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS),
        key=lambda path: str(path.relative_to(root)).lower(),
    )


def _cherry_source() -> Path:
    candidates = [
        ROOT / "Cherry_帧序列处理工具_2" / "web_temporal_smooth.py",
        ROOT / "Cherry_帧序列处理工具_1" / "web_temporal_smooth.py",
        ROOT / "Cherry_帧序列处理工具" / "web_temporal_smooth.py",
    ]
    for source in candidates:
        if source.exists():
            return source
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Cherry smoothing once for the current matte files.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-label", default="")
    args = parser.parse_args()

    src = Path(args.input_dir).resolve()
    dst = Path(args.output_dir).resolve()
    if not src.is_dir():
        raise NotADirectoryError(src)
    files = _collect_images(src)
    if not files:
        raise RuntimeError(f"input dir has no images: {src}")
    dst.mkdir(parents=True, exist_ok=True)

    run_dir = ROOT / "storage" / "manual_cherry_runs" / (args.run_label or _run_label())
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "config.json"
    config = {
        "source_path": str(_cherry_source()),
        "input_dir": str(src),
        "output_dir": str(dst),
        "files": [str(path) for path in files],
        "options": {
            "use_smooth": True,
            "smooth_window": 5,
            "smooth_sigma": 1.0,
            "min_alpha": 0.05,
            "sync_rgb": True,
            "ring_width": 25,
            "use_resize": True,
            "resize_width": 256,
            "resize_height": 256,
            "use_sharpen": True,
            "sharpen_amount": 2.0,
            "sharpen_radius": 2,
            "sharpen_threshold": 0.02,
            "sharpen_shrink": 4,
        },
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event": "started", "config_path": str(config_path), "total": len(files), "source_path": config["source_path"]}, ensure_ascii=False), flush=True)
    worker = ROOT / "scripts" / "cherry_batch_worker.py"
    proc = subprocess.run([sys.executable, str(worker), str(config_path)], cwd=str(ROOT))
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

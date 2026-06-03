from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path("E:/assetclaw-matting-bot")
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _count_png(path: Path) -> int:
    return sum(1 for _ in path.rglob("*.png")) if path.exists() else 0


def _log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume an animation automation workspace.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--workflow", default="")
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()

    workspace = Path(args.workspace)
    frames = workspace / "frames"
    matte = workspace / "matte"
    smooth = workspace / "smooth"
    if not frames.exists():
        raise FileNotFoundError(f"frames directory not found: {frames}")

    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.config import settings
    from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start
    from assetclaw_matting.skills.comfyui_skills import run_status as comfy_status
    from assetclaw_matting.skills.cherry_skills import run_start as cherry_start
    from assetclaw_matting.skills.cherry_skills import run_status as cherry_status

    init_db(settings.data_db_path)
    create_tables()

    before = {
        "frames": _count_png(frames),
        "matte": _count_png(matte),
        "smooth": _count_png(smooth),
    }
    _log("[resume] " + json.dumps({"workspace": str(workspace), "before": before}, ensure_ascii=False))

    if before["frames"] > before["matte"]:
        comfy = comfy_start(
            workflow_path=args.workflow or None,
            input_dir=str(frames),
            output_dir=str(matte),
            recursive=True,
            preserve_structure=True,
            max_images=50000,
            skip_existing=True,
            notify_interval_seconds=300,
        )
        _log("[comfyui] started " + json.dumps(comfy, ensure_ascii=False))
        _wait(lambda: comfy_status(comfy["run_id"], include_gpu=True), "comfyui", args.poll_seconds)
    else:
        _log("[comfyui] skipped: matte is already caught up")

    after_matte = _count_png(matte)
    after_smooth = _count_png(smooth)
    if after_matte > after_smooth:
        cherry = cherry_start(
            input_dir=str(matte),
            output_dir=str(smooth),
            recursive=True,
            max_images=50000,
            skip_existing=True,
            notify_interval_seconds=300,
        )
        _log("[cherry] started " + json.dumps(cherry, ensure_ascii=False))
        _wait(lambda: cherry_status(cherry["run_id"], include_gpu=True), "cherry", args.poll_seconds)
    else:
        _log("[cherry] skipped: smooth is already caught up")

    final = {
        "frames": _count_png(frames),
        "matte": _count_png(matte),
        "smooth": _count_png(smooth),
    }
    _log("[done] " + json.dumps({"workspace": str(workspace), "final": final}, ensure_ascii=False))
    return 0


def _wait(fn, label: str, poll_seconds: int) -> dict:
    last = ""
    while True:
        status = fn()
        text = (
            f"{status.get('status')} "
            f"{status.get('completed', 0)}/{status.get('total', 0)} "
            f"failed={status.get('failed', 0)}"
        )
        if text != last:
            _log(f"[{label}] {text}")
            last = text
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
                raise RuntimeError(f"{label} failed: {json.dumps(status, ensure_ascii=False)}")
            return status
        time.sleep(max(5, poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())

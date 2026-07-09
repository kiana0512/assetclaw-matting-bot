from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _init_runtime() -> None:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()


def _copy_frames_to_matte(date_root: Path, overwrite: bool) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for asset_kind in ("scene", "emoji"):
        for variant in ("default", "temporal_smooth"):
            frames = date_root / asset_kind / variant / "frames"
            matte = date_root / asset_kind / variant / "matte"
            if not frames.is_dir() or not any(frames.rglob("*.png")):
                continue
            if matte.exists() and overwrite:
                shutil.rmtree(matte)
            matte.mkdir(parents=True, exist_ok=True)
            count = 0
            for src in frames.rglob("*.png"):
                dst = matte / src.relative_to(frames)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                count += 1
            copied.append({"route": f"{asset_kind}/{variant}", "frames": str(frames), "matte": str(matte), "count": count})
    return copied


def _cherry_options(asset_kind: str, variant: str) -> dict[str, Any]:
    width, height = (384, 512) if asset_kind == "scene" else (256, 256)
    return {"use_smooth": variant == "temporal_smooth", "resize_width": width, "resize_height": height}


def _wait_cherry(run_id: str, poll_seconds: int) -> dict[str, Any]:
    from assetclaw_matting.skills.cherry_skills import run_status

    while True:
        status = run_status(run_id, include_gpu=False)
        print(json.dumps({"event": "cherry_status", "run_id": run_id, "status": status}, ensure_ascii=False), flush=True)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(poll_seconds)


def _run_cherry(date_root: Path, poll_seconds: int, skip_existing: bool) -> list[dict[str, Any]]:
    from assetclaw_matting.skills.cherry_skills import run_start

    results: list[dict[str, Any]] = []
    for asset_kind in ("scene", "emoji"):
        for variant in ("default", "temporal_smooth"):
            matte = date_root / asset_kind / variant / "matte"
            smooth = date_root / asset_kind / variant / "smooth"
            if not matte.is_dir() or not any(matte.rglob("*.png")):
                continue
            if smooth.exists() and not skip_existing:
                shutil.rmtree(smooth)
            options = _cherry_options(asset_kind, variant)
            print(
                json.dumps(
                    {
                        "event": "cherry_start",
                        "route": f"{asset_kind}/{variant}",
                        "input": str(matte),
                        "output": str(smooth),
                        "options": options,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            started = run_start(
                input_dir=str(matte),
                output_dir=str(smooth),
                recursive=True,
                max_images=50000,
                skip_existing=skip_existing,
                notify_interval_seconds=300,
                **options,
            )
            status = _wait_cherry(str(started["run_id"]), poll_seconds)
            results.append(
                {
                    "route": f"{asset_kind}/{variant}",
                    "run_id": started["run_id"],
                    "options": options,
                    "status": status.get("status"),
                    "completed": status.get("completed"),
                    "failed": status.get("failed"),
                    "smooth": str(smooth),
                    "png_count": len([p for p in smooth.rglob("*.png") if p.is_file()]) if smooth.exists() else 0,
                }
            )
            if status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
                raise RuntimeError(f"Cherry failed for {asset_kind}/{variant}: {status}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Fake matte by copying frames, then run real Cherry and build unity_ready.")
    parser.add_argument("--date-root", required=True)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    date_root = Path(args.date_root).resolve()
    _init_runtime()
    copied = _copy_frames_to_matte(date_root, overwrite=bool(args.overwrite))
    cherry = _run_cherry(date_root, poll_seconds=max(2, args.poll_seconds), skip_existing=bool(args.skip_existing))

    from tools.animation_automation.core import build_unity_ready, format_unity_ready_summary

    ready = build_unity_ready(date_root, overwrite=True)
    report = {"ok": True, "date_root": str(date_root), "fake_matte": copied, "cherry": cherry, "unity_ready": ready}
    report_path = ROOT / "storage" / "debug" / f"fake_matte_cherry_unity_ready_{date_root.name}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(format_unity_ready_summary(date_root, ready), flush=True)
    print(json.dumps({"event": "report", "path": str(report_path), "cherry": cherry}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

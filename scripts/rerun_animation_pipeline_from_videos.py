from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "feishu_frame_tool"
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}


def _now_label() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _archive_existing(root: Path, names: list[str]) -> Path:
    backup_root = root / "_rerun_backups" / _now_label()
    backup_root.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = root / name
        if src.exists():
            shutil.move(str(src), str(backup_root / name))
    return backup_root


def _video_files(video_root: Path) -> list[Path]:
    return sorted((p for p in video_root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS), key=lambda p: str(p).lower())


def _extract_frames(root: Path, fps: int) -> dict[str, object]:
    sys.path.insert(0, str(TOOL_DIR))
    from extractor import LocalFrameExtractor

    video_root = root / "videos"
    frame_root = root / "frames"
    frame_root.mkdir(parents=True, exist_ok=True)
    extractor = LocalFrameExtractor(str(frame_root), fps=fps, max_frames=0, logger=lambda message: print(message, flush=True))
    items = []
    for video in _video_files(video_root):
        rel_dir = video.parent.relative_to(video_root)
        dest = Path(extractor.process_video(str(video), str(rel_dir)))
        count = len(list(dest.glob("*.png")))
        items.append({"video": str(video), "frame_dir": str(dest), "rel_dir": str(rel_dir).replace("\\", "/"), "frame_count": count})
    total = sum(int(item["frame_count"]) for item in items)
    manifest = {"items": items, "frame_count": total, "fps": fps, "max_frames": 0}
    (frame_root / "_pipeline_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _init_app_db() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()


def _wait_comfy(run_id: str, poll_seconds: int) -> dict[str, object]:
    from assetclaw_matting.skills.comfyui_skills import run_status

    while True:
        status = run_status(run_id, include_gpu=False)
        print(json.dumps({"step": "comfyui", "status": status}, ensure_ascii=False), flush=True)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(poll_seconds)


def _wait_cherry(run_id: str, poll_seconds: int) -> dict[str, object]:
    from assetclaw_matting.skills.cherry_skills import run_status

    while True:
        status = run_status(run_id, include_gpu=False)
        print(json.dumps({"step": "cherry", "status": status}, ensure_ascii=False), flush=True)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(poll_seconds)


def _run_comfy_and_cherry(root: Path, frame_count: int, workflow_path: str | None, poll_seconds: int) -> dict[str, object]:
    _init_app_db()
    from assetclaw_matting.skills.cherry_skills import run_start as cherry_start
    from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start

    frames = root / "frames"
    matte = root / "matte"
    smooth = root / "smooth"
    matte.mkdir(parents=True, exist_ok=True)
    smooth.mkdir(parents=True, exist_ok=True)
    comfy = comfy_start(
        workflow_path=workflow_path,
        input_dir=str(frames),
        output_dir=str(matte),
        recursive=True,
        preserve_structure=True,
        skip_existing=False,
        notify_interval_seconds=300,
    )
    comfy_status = _wait_comfy(str(comfy["run_id"]), poll_seconds)
    if comfy_status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
        return {"ok": False, "comfyui": comfy_status, "cherry": None}
    matte_count = len([p for p in matte.rglob("*.png") if p.is_file()])
    if matte_count != frame_count:
        return {
            "ok": False,
            "comfyui": comfy_status,
            "cherry": None,
            "error": f"matte count {matte_count} != frame count {frame_count}",
        }
    cherry = cherry_start(
        input_dir=str(matte),
        output_dir=str(smooth),
        recursive=True,
        skip_existing=False,
        notify_interval_seconds=300,
    )
    cherry_status = _wait_cherry(str(cherry["run_id"]), poll_seconds)
    return {"ok": cherry_status.get("status") in {"DONE", "DONE_WITH_ERRORS"}, "comfyui": comfy_status, "cherry": cherry_status}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild animation frames, matte, and smooth outputs from existing videos.")
    parser.add_argument("--root", default=str(ROOT.parent / "animation_auto" / datetime.now().strftime("%Y-%m-%d")))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--workflow-path", default="")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--extract-only", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / "videos").is_dir():
        raise FileNotFoundError(root / "videos")
    backup_root = _archive_existing(root, ["frames", "frames_missing_patch", "matte", "smooth"])
    print(json.dumps({"event": "archived", "backup_root": str(backup_root)}, ensure_ascii=False), flush=True)
    manifest = _extract_frames(root, fps=args.fps)
    report: dict[str, object] = {
        "ok": True,
        "root": str(root),
        "backup_root": str(backup_root),
        "fps": args.fps,
        "frame_count": manifest["frame_count"],
        "sequence_count": len(manifest["items"]),
    }
    if not args.extract_only:
        report["pipeline"] = _run_comfy_and_cherry(
            root=root,
            frame_count=int(manifest["frame_count"]),
            workflow_path=args.workflow_path or None,
            poll_seconds=max(5, args.poll_seconds),
        )
        report["ok"] = bool(report["pipeline"].get("ok")) if isinstance(report["pipeline"], dict) else False
    report_dir = ROOT / "storage" / "animation_reruns"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"rerun_{_now_label()}.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "feishu_frame_tool"


def _save_png(path: Path, frame) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError(f"PNG encode failed: {path}")
    buf.tofile(str(path))


def _video_files(video_root: Path) -> list[Path]:
    exts = {".mp4", ".mov", ".webm", ".m4v"}
    return sorted((p for p in video_root.rglob("*") if p.is_file() and p.suffix.lower() in exts), key=lambda p: str(p).lower())


def _expected_names(max_frames: int) -> set[str]:
    return {f"{idx:04d}.png" for idx in range(1, max_frames + 1)}


def collect_missing(root: Path, max_frames: int) -> list[dict[str, object]]:
    video_root = root / "videos"
    frame_root = root / "frames"
    expected = _expected_names(max_frames)
    items: list[dict[str, object]] = []
    for video in _video_files(video_root):
        rel_dir = video.parent.relative_to(video_root)
        frame_dir = frame_root / rel_dir
        existing = {p.name for p in frame_dir.glob("*.png")} if frame_dir.exists() else set()
        missing_names = sorted(expected - existing)
        if missing_names:
            items.append(
                {
                    "video": str(video),
                    "rel_dir": str(rel_dir).replace("\\", "/"),
                    "missing": missing_names,
                    "existing_count": len(existing),
                }
            )
    return items


def extract_missing_for_video(
    video: Path,
    rel_dir: Path,
    missing_names: Iterable[str],
    frame_root: Path,
    patch_root: Path,
    fps: int,
    max_frames: int,
    overwrite_patch: bool,
) -> list[str]:
    import cv2

    wanted = {int(Path(name).stem) for name in missing_names}
    saved: list[str] = []
    tmp = None
    read_path = str(video)
    if not str(video).isascii():
        fd, tmp = tempfile.mkstemp(suffix=video.suffix or ".mp4")
        os.close(fd)
        shutil.copyfile(video, tmp)
        read_path = tmp

    try:
        cap = cv2.VideoCapture(read_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video}")
        src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        resample = bool(fps and src_fps and fps < src_fps)
        emit_interval = 1.0 / fps if fps else 0.0
        next_emit = 0.0
        frame_idx = 0
        out_idx = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                keep = True
                if resample:
                    cur_time = frame_idx / src_fps
                    if cur_time + 1e-9 >= next_emit:
                        next_emit += emit_interval
                    else:
                        keep = False
                if keep:
                    out_idx += 1
                    if out_idx in wanted:
                        name = f"{out_idx:04d}.png"
                        main_target = frame_root / rel_dir / name
                        patch_target = patch_root / rel_dir / name
                        if not main_target.exists():
                            _save_png(main_target, frame)
                        if overwrite_patch or not patch_target.exists():
                            _save_png(patch_target, frame)
                        saved.append(str(Path(rel_dir) / name).replace("\\", "/"))
                    if max_frames and out_idx >= max_frames:
                        break
                frame_idx += 1
        finally:
            cap.release()
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return saved


def prepare_missing_frames(root: Path, fps: int, max_frames: int, patch_dir: Path, overwrite_patch: bool) -> dict[str, object]:
    items = collect_missing(root, max_frames)
    frame_root = root / "frames"
    patch_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    errors: list[dict[str, str]] = []
    for item in items:
        video = Path(str(item["video"]))
        rel_dir = Path(str(item["rel_dir"]))
        missing_names = list(item["missing"])
        try:
            saved.extend(
                extract_missing_for_video(
                    video=video,
                    rel_dir=rel_dir,
                    missing_names=missing_names,
                    frame_root=frame_root,
                    patch_root=patch_dir,
                    fps=fps,
                    max_frames=max_frames,
                    overwrite_patch=overwrite_patch,
                )
            )
        except Exception as exc:
            errors.append({"video": str(video), "error": str(exc)})
    report = {
        "ok": not errors,
        "root": str(root),
        "fps": fps,
        "max_frames": max_frames,
        "patch_dir": str(patch_dir),
        "sequence_count": len(items),
        "missing_count": sum(len(item["missing"]) for item in items),
        "saved_count": len(saved),
        "saved": saved,
        "errors": errors,
    }
    return report


def _init_app_db() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()


def _wait_for_comfy(run_id: str, poll_seconds: int) -> dict[str, object]:
    from assetclaw_matting.skills.comfyui_skills import run_status

    while True:
        status = run_status(run_id, include_gpu=False)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(poll_seconds)


def _wait_for_cherry(run_id: str, poll_seconds: int) -> dict[str, object]:
    from assetclaw_matting.skills.cherry_skills import run_status

    while True:
        status = run_status(run_id, include_gpu=False)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(poll_seconds)


def start_patch_pipeline(root: Path, patch_dir: Path, workflow_path: str | None, poll_seconds: int) -> dict[str, object]:
    _init_app_db()
    from assetclaw_matting.skills.cherry_skills import run_start as cherry_start
    from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start

    matte_dir = root / "matte"
    smooth_dir = root / "smooth"
    comfy = comfy_start(
        workflow_path=workflow_path,
        input_dir=str(patch_dir),
        output_dir=str(matte_dir),
        recursive=True,
        preserve_structure=True,
        skip_existing=True,
        notify_interval_seconds=300,
    )
    comfy_status = _wait_for_comfy(str(comfy["run_id"]), poll_seconds)
    if comfy_status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
        return {"ok": False, "comfyui": comfy_status, "cherry": None}

    cherry = cherry_start(
        input_dir=str(matte_dir),
        output_dir=str(smooth_dir),
        recursive=True,
        skip_existing=False,
        notify_interval_seconds=300,
    )
    cherry_status = _wait_for_cherry(str(cherry["run_id"]), poll_seconds)
    return {"ok": cherry_status.get("status") in {"DONE", "DONE_WITH_ERRORS"}, "comfyui": comfy_status, "cherry": cherry_status}


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair dedup-removed animation frames and optionally run matting/smoothing.")
    parser.add_argument("--root", default=r"E:\animation_automation\2026-06-02")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--max-frames", type=int, default=24)
    parser.add_argument("--patch-dir", default="")
    parser.add_argument("--workflow-path", default="")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--no-overwrite-patch", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    patch_dir = Path(args.patch_dir).resolve() if args.patch_dir else root / "frames_missing_patch"
    run_tag = "repair_" + uuid.uuid4().hex[:10]
    report_dir = ROOT / "storage" / "missing_frame_repairs"
    report_dir.mkdir(parents=True, exist_ok=True)

    report = prepare_missing_frames(
        root=root,
        fps=args.fps,
        max_frames=args.max_frames,
        patch_dir=patch_dir,
        overwrite_patch=not args.no_overwrite_patch,
    )
    if not report["ok"]:
        (report_dir / f"{run_tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 1

    patch_image_count = len([p for p in patch_dir.rglob("*.png") if p.is_file()])
    report["patch_image_count"] = patch_image_count
    if not args.prepare_only and patch_image_count > 0:
        report["pipeline"] = start_patch_pipeline(
            root=root,
            patch_dir=patch_dir,
            workflow_path=args.workflow_path or None,
            poll_seconds=max(5, args.poll_seconds),
        )

    report_path = report_dir / f"{run_tag}.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

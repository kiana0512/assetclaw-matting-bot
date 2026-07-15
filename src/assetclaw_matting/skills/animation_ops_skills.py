from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.media_skills import IMAGE_EXTS, VIDEO_EXTS
from assetclaw_matting.skills.security import validate_path


def _default_root() -> str:
    from assetclaw_matting.config import settings

    return str(Path(settings.animation_root) / datetime.now().strftime("%Y-%m-%d"))


def status(root: str | None = None, include_runs: bool = True) -> dict[str, Any]:
    root = root or _default_root()
    workspace = _workspace(root, must_exist=True)
    dirs = _standard_dirs(workspace)
    counts = {
        "videos": _count_files(dirs["videos"], VIDEO_EXTS),
        "frames": _count_files(dirs["frames"], IMAGE_EXTS),
        "matte": _count_files(dirs["matte"], IMAGE_EXTS),
        "smooth": _count_files(dirs["smooth"], IMAGE_EXTS),
    }
    sequences = {
        name: _sequence_count(path, IMAGE_EXTS if name != "videos" else VIDEO_EXTS)
        for name, path in dirs.items()
    }
    payload: dict[str, Any] = {
        "ok": True,
        "root": str(workspace),
        "dirs": {name: str(path) for name, path in dirs.items()},
        "counts": counts,
        "sequences": sequences,
        "matte_matches_frames": counts["matte"] == counts["frames"] if counts["frames"] else False,
        "smooth_matches_matte": counts["smooth"] == counts["matte"] if counts["matte"] else False,
        "latest_backup": _latest_child(workspace / "_rerun_backups"),
        "latest_rerun_report": _latest_report("animation_reruns", "rerun_*.json"),
        "latest_manual_smooth_config": _latest_report("manual_cherry_runs", "*/config.json"),
    }
    if include_runs:
        payload["runs"] = _active_runs()
    return payload


def manual_smooth_current(
    root: str | None = None,
    input_dir: str | None = None,
    output_dir: str | None = None,
    skip_existing: bool = False,
    notify_interval_seconds: int = 300,
    use_smooth: bool = False,
    resize_width: int = 384,
    resize_height: int = 512,
) -> dict[str, Any]:
    from assetclaw_matting.skills.cherry_skills import preset_options, run_start

    workspace = _workspace(root or _default_root(), must_exist=True)
    src = validate_path(input_dir or str(workspace / "matte"), must_exist=True)
    dst = validate_path(output_dir or str(workspace / "smooth"), must_exist=False)
    profile = _cherry_profile_from_path(src)
    if profile == "half" and int(resize_width) == 384 and int(resize_height) == 512:
        resize_width, resize_height = 256, 256
    cherry_options = preset_options(profile, use_smooth=bool(use_smooth))
    cherry_options["resize_width"] = int(resize_width)
    cherry_options["resize_height"] = int(resize_height)
    cherry_options["resize1_width"] = int(resize_width)
    cherry_options["resize1_height"] = int(resize_height)
    cherry_options["resize2_width"] = int(resize_width)
    cherry_options["resize2_height"] = int(resize_height)
    result = run_start(
        input_dir=str(src),
        output_dir=str(dst),
        recursive=True,
        max_images=50000,
        skip_existing=bool(skip_existing),
        notify_interval_seconds=int(notify_interval_seconds),
        **cherry_options,
    )
    return {
        "ok": True,
        "root": str(workspace),
        "input_dir": str(src),
        "output_dir": str(dst),
        "cherry": result,
    }


def rerun_from_videos(
    root: str | None = None,
    fps: int = 24,
    workflow_path: str | None = None,
    poll_seconds: int = 30,
    extract_only: bool = False,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    workspace = _workspace(root or _default_root(), must_exist=True)
    videos = workspace / "videos"
    if not videos.is_dir():
        raise FileNotFoundError(str(videos))
    script = Path(settings.assetclaw_root) / "scripts" / "rerun_animation_pipeline_from_videos.py"
    if not script.exists():
        raise FileNotFoundError(str(script))

    run_label = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(settings.storage_dir) / "animation_reruns"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"rerun_{run_label}.log"
    args = [
        sys.executable,
        str(script),
        "--root",
        str(workspace),
        "--fps",
        str(int(fps)),
        "--poll-seconds",
        str(max(5, int(poll_seconds))),
    ]
    if workflow_path:
        args.extend(["--workflow-path", str(validate_path(workflow_path, must_exist=True))])
    if extract_only:
        args.append("--extract-only")

    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        args,
        cwd=str(settings.assetclaw_root),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return {
        "ok": True,
        "root": str(workspace),
        "pid": proc.pid,
        "log_path": str(log_path),
        "fps": int(fps),
        "extract_only": bool(extract_only),
        "message": "已在后台启动全量重跑。它会先归档 frames/frames_missing_patch/matte/smooth，再从 videos 重新抽帧。",
    }


def preview_manual_smooth_current_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    try:
        workspace = _workspace(arguments.get("root") or _default_root(), must_exist=True)
        src = validate_path(arguments.get("input_dir") or str(workspace / "matte"), must_exist=True)
        dst = validate_path(arguments.get("output_dir") or str(workspace / "smooth"), must_exist=False)
        total = _count_files(src, IMAGE_EXTS)
        profile = _cherry_profile_from_path(src)
        width = int(arguments.get("resize_width") or 384)
        height = int(arguments.get("resize_height") or 512)
        if profile == "half" and width == 384 and height == 512:
            width, height = 256, 256
        lines = [
            "请确认是否基于当前 matte 重新做 Cherry 平滑：",
            f"工作区：{workspace}",
            f"输入：{src}",
            f"输出：{dst}",
            f"当前可处理图片：{total} 张",
            f"跳过已有输出：{'是' if arguments.get('skip_existing') else '否'}",
            f"后处理：{width}x{height}，时序平滑：{'开' if arguments.get('use_smooth') else '关'}",
            f"回复：确认执行 {confirmation_id}",
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"需要确认：animation.manual_smooth_current\n预检查失败：{exc}\n回复：确认执行 {confirmation_id}"


def preview_rerun_from_videos_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    try:
        workspace = _workspace(arguments.get("root") or _default_root(), must_exist=True)
        counts = status(str(workspace), include_runs=False)["counts"]
        lines = [
            "请确认是否从 videos 全量重做动画流程：",
            f"工作区：{workspace}",
            "会归档并重建：frames、frames_missing_patch、matte、smooth",
            f"当前数量：videos {counts['videos']}，frames {counts['frames']}，matte {counts['matte']}，smooth {counts['smooth']}",
            f"fps：{int(arguments.get('fps') or 24)}，只抽帧：{'是' if arguments.get('extract_only') else '否'}",
            f"回复：确认执行 {confirmation_id}",
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"需要确认：animation.rerun_from_videos\n预检查失败：{exc}\n回复：确认执行 {confirmation_id}"


def _workspace(root: str, must_exist: bool) -> Path:
    path = validate_path(root, must_exist=must_exist)
    if must_exist and not path.is_dir():
        raise NotADirectoryError(str(path))
    return path


def _standard_dirs(root: Path) -> dict[str, Path]:
    return {
        "videos": root / "videos",
        "frames": root / "frames",
        "matte": root / "matte",
        "smooth": root / "smooth",
    }


def _cherry_profile_from_path(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    return "half" if "emoji" in parts else "full"


def _count_files(root: Path, extensions: set[str]) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions)


def _sequence_count(root: Path, extensions: set[str]) -> int:
    if not root.is_dir():
        return 0
    parents = {path.parent for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions}
    return len(parents)


def _latest_child(root: Path) -> str:
    if not root.is_dir():
        return ""
    children = sorted(root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
    return str(children[0]) if children else ""


def _latest_report(folder: str, pattern: str) -> str:
    from assetclaw_matting.config import settings

    root = Path(settings.storage_dir) / folder
    if not root.exists():
        return ""
    files = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return str(files[0]) if files else ""


def _active_runs() -> dict[str, Any]:
    runs: dict[str, Any] = {}
    try:
        from assetclaw_matting.skills.comfyui_skills import run_list as comfy_list

        runs["comfyui"] = comfy_list(limit=3, include_archived=False, include_finished=False)
    except Exception as exc:
        runs["comfyui"] = {"ok": False, "error": str(exc)}
    try:
        from assetclaw_matting.skills.cherry_skills import run_list as cherry_list

        runs["cherry"] = cherry_list(limit=3, include_archived=False, include_finished=False)
    except Exception as exc:
        runs["cherry"] = {"ok": False, "error": str(exc)}
    try:
        from assetclaw_matting.skills.pipeline_skills import run_list as pipeline_list

        runs["pipeline"] = pipeline_list(limit=3, include_finished=False)
    except Exception as exc:
        runs["pipeline"] = {"ok": False, "error": str(exc)}
    return runs

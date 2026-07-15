from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "feishu_frame_tool"
IDLE_NAMES = {"idle", "idel", "待机"}


def _now_label() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _init_imports() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(TOOL_DIR))


def _init_app_db() -> None:
    from assetclaw_matting.config import settings
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()


def _load_config(path: Path, workspace: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("paths", {})
    config["paths"]["download_dir"] = str(workspace / "videos")
    config["paths"]["export_dir"] = str(workspace / "frames")
    config.setdefault("framepacker", {})
    config["framepacker"]["fps"] = 24
    config["framepacker"]["max_frames"] = 0
    config.setdefault("dedup", {})
    config["dedup"]["enabled"] = False
    return config


def _is_idle(identity: dict[str, Any]) -> bool:
    emotion = str(identity.get("emotion") or "").lower()
    animation_name = str(identity.get("animation_name") or "").lower()
    return emotion in IDLE_NAMES or animation_name in IDLE_NAMES


def _archive_existing(root: Path, rel_dirs: list[str], label: str) -> Path:
    backup_root = root / "_idle_replacements" / label / "backup"
    for section in ("videos", "frames", "matte", "smooth"):
        for rel_dir in rel_dirs:
            src = root / section / rel_dir
            if not src.exists():
                continue
            dst = backup_root / section / rel_dir
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
    return backup_root


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _copy_replace(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(str(src))
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def _count_png(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*.png") if path.is_file())


def _download_and_extract_idle(config: dict[str, Any], workspace: Path, patch_frames: Path) -> dict[str, Any]:
    from extractor import LocalFrameExtractor
    from workflow import Workflow, _attachments

    workflow = Workflow(config, logger=lambda message: print(message, flush=True))
    records = workflow.client.list_records()
    workflow.rec_map = {record.get("record_id", ""): record for record in records}
    animation_records = [record for record in records if _attachments(record.get("fields", {}).get(workflow.f_animation))]
    extractor = LocalFrameExtractor(str(patch_frames), fps=workflow.fps, max_frames=0, logger=lambda message: print(message, flush=True))

    items: list[dict[str, Any]] = []
    for record in animation_records:
        rid = record.get("record_id", "")
        fields = record.get("fields", {})
        identity = workflow._record_identity(rid, fields)
        if not _is_idle(identity):
            continue
        role = identity["role"]
        emotion = "idle"
        rel_dir = str(Path(role) / emotion)
        videos = [
            attachment
            for attachment in _attachments(fields.get(workflow.f_animation))
            if str(attachment.get("type", "")).startswith("video")
            or str(attachment.get("name", "")).lower().endswith((".mp4", ".mov", ".webm", ".m4v"))
        ]
        if not videos:
            continue

        video_dir = workspace / "videos" / rel_dir
        _clear_dir(video_dir)
        frame_dir = patch_frames / rel_dir
        _clear_dir(frame_dir)
        downloaded = []
        for index, attachment in enumerate(videos):
            suffix = Path(str(attachment.get("name") or "")).suffix or ".mp4"
            name = f"{role}_{emotion}{'' if len(videos) == 1 else f'_{index + 1}'}{suffix}"
            print(json.dumps({"event": "download", "role": role, "emotion": emotion, "file": name}, ensure_ascii=False), flush=True)
            downloaded.append(
                workflow.client.download_attachment(
                    attachment,
                    str(video_dir),
                    field_name=workflow.f_animation,
                    record_id=rid,
                    save_name=name,
                )
            )
        for index, video in enumerate(downloaded):
            out_subdir = rel_dir if len(downloaded) == 1 else str(Path(rel_dir) / f"video_{index + 1}")
            dest = Path(extractor.process_video(video, out_subdir))
            count = _count_png(dest)
            items.append({
                **identity,
                "emotion": emotion,
                "rel_dir": rel_dir.replace("\\", "/"),
                "video_path": video,
                "frame_dir": str(dest),
                "frame_count": count,
            })
            print(json.dumps({"event": "extracted", "role": role, "emotion": emotion, "frames": count}, ensure_ascii=False), flush=True)
    return {"items": items, "count": len(items), "frame_count": sum(int(item["frame_count"]) for item in items)}


def _discover_idle_rel_dirs(config: dict[str, Any]) -> list[str]:
    from workflow import Workflow, _attachments

    workflow = Workflow(config, logger=lambda message: print(message, flush=True))
    records = workflow.client.list_records()
    workflow.rec_map = {record.get("record_id", ""): record for record in records}
    rel_dirs: set[str] = set()
    for record in records:
        fields = record.get("fields", {})
        if not _attachments(fields.get(workflow.f_animation)):
            continue
        identity = workflow._record_identity(record.get("record_id", ""), fields)
        if _is_idle(identity):
            rel_dirs.add(str(Path(identity["role"]) / "idle"))
    return sorted(rel_dirs)


def _wait_comfy(run_id: str, poll_seconds: int) -> dict[str, Any]:
    from assetclaw_matting.skills.comfyui_skills import run_status

    while True:
        status = run_status(run_id, include_gpu=False)
        print(json.dumps({"step": "comfyui", "run_id": run_id, "status": status.get("status"), "completed": status.get("completed"), "total": status.get("total")}, ensure_ascii=False), flush=True)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(poll_seconds)


def _wait_cherry(run_id: str, poll_seconds: int) -> dict[str, Any]:
    from assetclaw_matting.skills.cherry_skills import run_status

    while True:
        status = run_status(run_id, include_gpu=False)
        print(json.dumps({"step": "cherry", "run_id": run_id, "status": status.get("status"), "completed": status.get("completed"), "total": status.get("total")}, ensure_ascii=False), flush=True)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(poll_seconds)


def _run_patch_pipeline(root: Path, patch_frames: Path, patch_matte: Path, patch_smooth: Path, poll_seconds: int) -> dict[str, Any]:
    from assetclaw_matting.skills.cherry_skills import run_start as cherry_start
    from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start

    comfy = comfy_start(
        input_dir=str(patch_frames),
        output_dir=str(patch_matte),
        recursive=True,
        preserve_structure=True,
        max_images=50000,
        skip_existing=False,
        notify_interval_seconds=300,
    )
    comfy_status = _wait_comfy(str(comfy["run_id"]), poll_seconds)
    if comfy_status.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
        return {"ok": False, "comfyui": comfy_status}

    cherry = cherry_start(
        input_dir=str(patch_matte),
        output_dir=str(patch_smooth),
        recursive=True,
        max_images=50000,
        skip_existing=False,
        notify_interval_seconds=300,
    )
    cherry_status = _wait_cherry(str(cherry["run_id"]), poll_seconds)
    return {
        "ok": cherry_status.get("status") in {"DONE", "DONE_WITH_ERRORS"},
        "comfyui": comfy_status,
        "cherry": cherry_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replace only idle videos/frames/matte/smooth from the configured Feishu table.")
    parser.add_argument("--root", default=str(ROOT.parent / "animation_auto" / datetime.now().strftime("%Y-%m-%d")))
    parser.add_argument("--config", default=str(TOOL_DIR / "config.json"))
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()

    _init_imports()
    _init_app_db()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    label = _now_label()
    work_root = root / "_idle_replacements" / label / "work"
    patch_frames = work_root / "frames"
    patch_matte = work_root / "matte"
    patch_smooth = work_root / "smooth"
    for path in (patch_frames, patch_matte, patch_smooth):
        path.mkdir(parents=True, exist_ok=True)

    config = _load_config(Path(args.config), root)
    rel_dirs = _discover_idle_rel_dirs(config)
    if not rel_dirs:
        raise RuntimeError("No idle records with animation attachments were found in Feishu.")
    backup_root = _archive_existing(root, rel_dirs, label)
    extracted = _download_and_extract_idle(config, root, patch_frames)
    extracted_rel_dirs = sorted({str(item["rel_dir"]).replace("/", "\\") for item in extracted["items"]})
    missing = [rel_dir for rel_dir in rel_dirs if rel_dir not in extracted_rel_dirs]
    if missing:
        raise RuntimeError(f"Idle records were discovered but not extracted: {missing}")
    pipeline = _run_patch_pipeline(root, patch_frames, patch_matte, patch_smooth, max(5, int(args.poll_seconds)))
    if not pipeline.get("ok"):
        report = {"ok": False, "root": str(root), "label": label, "backup_root": str(backup_root), "extracted": extracted, "pipeline": pipeline}
        _write_report(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 1

    for rel_dir in rel_dirs:
        _copy_replace(patch_frames / rel_dir, root / "frames" / rel_dir)
        _copy_replace(patch_matte / rel_dir, root / "matte" / rel_dir)
        _copy_replace(patch_smooth / rel_dir, root / "smooth" / rel_dir)

    report = {
        "ok": True,
        "root": str(root),
        "label": label,
        "backup_root": str(backup_root),
        "work_root": str(work_root),
        "idle_sequence_count": len(rel_dirs),
        "frame_count": extracted["frame_count"],
        "extracted": extracted,
        "pipeline": pipeline,
        "replaced_rel_dirs": [rel_dir.replace("\\", "/") for rel_dir in rel_dirs],
    }
    report_path = _write_report(report)
    report["report_path"] = str(report_path)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


def _write_report(report: dict[str, Any]) -> Path:
    report_dir = ROOT / "storage" / "idle_replacements"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"idle_replace_{report['label']}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


if __name__ == "__main__":
    raise SystemExit(main())

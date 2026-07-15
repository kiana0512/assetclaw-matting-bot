from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "feishu_frame_tool"
CHECK = ROOT / "storage" / "real_pipeline_check"


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(TOOL))

    from workflow import load_config, _attachments
    from feishu_client import FeishuClient
    from extractor import LocalFrameExtractor
    from assetclaw_matting.db.schema import create_tables
    from assetclaw_matting.db.sqlite import init_db
    from assetclaw_matting.skills.comfyui_skills import run_start as comfy_start, run_status as comfy_status
    from assetclaw_matting.skills.cherry_skills import run_start as cherry_start, run_status as cherry_status

    init_db(ROOT / "data" / "assetclaw.db")
    create_tables()

    raw_dir = CHECK / "raw_videos"
    frames_dir = CHECK / "frames_all"
    subset_dir = CHECK / "frames_subset"
    matte_dir = CHECK / "matte"
    smooth_dir = CHECK / "smooth"
    if CHECK.exists():
        shutil.rmtree(CHECK)
    for path in (raw_dir, frames_dir, subset_dir, matte_dir, smooth_dir):
        path.mkdir(parents=True, exist_ok=True)

    cfg = load_config(str(TOOL / "config.json"))
    client = FeishuClient.from_feishu_config(cfg["feishu"], logger=lambda m: log(f"[feishu] {m}"))
    records = client.list_records()
    field = cfg["fields"]["animation"]
    selected = None
    for record in records:
        attachments = _attachments(record.get("fields", {}).get(field))
        for attachment in attachments:
            name = str(attachment.get("name") or "")
            typ = str(attachment.get("type") or "")
            if typ.startswith("video") or name.lower().endswith((".mp4", ".mov", ".webm", ".m4v")):
                selected = (record, attachment)
                break
        if selected:
            break
    if not selected:
        raise RuntimeError("表格里没有找到视频附件")

    record, attachment = selected
    record_id = record.get("record_id", "")
    log(f"[frame] selected record={record_id} attachment={attachment.get('name')}")
    video = client.download_attachment(attachment, str(raw_dir), field_name=field, record_id=record_id)
    log(f"[frame] downloaded {video}")

    extractor = LocalFrameExtractor(str(frames_dir), fps=24, max_frames=24, logger=lambda m: log(f"[frame] {m}"))
    exported = Path(extractor.process_video(video, "sample"))
    frames = sorted(exported.glob("*.png"))
    log(f"[frame] exported_frames={len(frames)} dir={exported}")
    if not frames:
        raise RuntimeError("抽帧没有产出 PNG")

    sample_dir = subset_dir / "sample"
    if sample_dir.exists():
        shutil.rmtree(sample_dir)
    sample_dir.mkdir(parents=True, exist_ok=True)
    for frame in frames[:24]:
        shutil.copy2(frame, sample_dir / frame.name)
    log(f"[frame] subset_frames={len(list(sample_dir.glob('*.png')))} dir={sample_dir}")

    log("[comfyui] starting sample matting run; if another run is active this should wait in queue")
    comfy = comfy_start(input_dir=str(subset_dir), output_dir=str(matte_dir), recursive=True, preserve_structure=True, max_images=24, notify_interval_seconds=300)
    comfy_id = comfy["run_id"]
    log(f"[comfyui] run_id={comfy_id}")
    comfy_final = _wait(lambda: comfy_status(comfy_id, include_gpu=True), {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}, "comfyui")
    if comfy_final.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
        raise RuntimeError("ComfyUI sample failed: " + json.dumps(comfy_final, ensure_ascii=False))

    log("[cherry] starting sample smoothing run")
    cherry = cherry_start(input_dir=str(matte_dir), output_dir=str(smooth_dir), recursive=True, notify_interval_seconds=300, use_resize=False, use_sharpen=True)
    cherry_id = cherry["run_id"]
    log(f"[cherry] run_id={cherry_id}")
    cherry_final = _wait(lambda: cherry_status(cherry_id, include_gpu=True), {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}, "cherry")
    if cherry_final.get("status") not in {"DONE", "DONE_WITH_ERRORS"}:
        raise RuntimeError("Cherry sample failed: " + json.dumps(cherry_final, ensure_ascii=False))

    result = {
        "video": video,
        "frames_dir": str(exported),
        "subset_dir": str(subset_dir),
        "matte_dir": str(matte_dir),
        "smooth_dir": str(smooth_dir),
        "comfyui": comfy_final,
        "cherry": cherry_final,
    }
    log("[done] " + json.dumps(result, ensure_ascii=False))
    return 0


def _wait(fn, done_statuses: set[str], label: str) -> dict:
    last = ""
    while True:
        status = fn()
        text = f"{status.get('status')} {status.get('completed', status.get('processed_records', 0))}/{status.get('total', status.get('total_records', 0))}"
        if text != last:
            log(f"[{label}] {text}")
            last = text
        if status.get("status") in done_statuses:
            return status
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())

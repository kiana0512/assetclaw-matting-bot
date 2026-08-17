from __future__ import annotations

import argparse
import json
import threading
import time

from assetclaw_matting.config import settings
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills import cherry_skills, direct_video_skills


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume a persisted Cherry child with larger native-raster batches, then package and deliver its parent."
    )
    parser.add_argument("parent_run_id")
    parser.add_argument("cherry_run_id")
    parser.add_argument("--max-files", type=int, default=24)
    parser.add_argument("--max-pixels", type=int, default=40_000_000)
    args = parser.parse_args()

    init_db(settings.data_db_path)
    settings.cherry_html_batch_max_files = max(1, args.max_files)
    settings.cherry_html_batch_max_pixels = max(1, args.max_pixels)

    child = cherry_skills._get_run(args.cherry_run_id)
    if not child:
        raise RuntimeError(f"Cherry run not found: {args.cherry_run_id}")
    cherry_skills._set_run_status(args.cherry_run_id, "RUNNING")

    parent = direct_video_skills._load(args.parent_run_id)
    if not parent:
        raise RuntimeError(f"Direct video run not found: {args.parent_run_id}")
    parent["status"] = "RUNNING"
    parent["stage"] = "resume_postprocess_fast"
    parent["worker_pid"] = 0
    parent["error"] = ""
    direct_video_skills._append_log(
        parent,
        f"Cherry 断点加速：重新挂接 {args.cherry_run_id}，单批上限 {settings.cherry_html_batch_max_files} 帧。",
    )
    direct_video_skills._save(parent)

    worker = threading.Thread(
        target=cherry_skills._run_worker,
        args=(args.cherry_run_id,),
        daemon=False,
    )
    worker.start()
    while worker.is_alive():
        payload = cherry_skills.run_status(args.cherry_run_id, include_gpu=False)
        parent = direct_video_skills._load(args.parent_run_id) or parent
        parent.setdefault("children", {})["cherry"] = payload
        parent["children"].setdefault("cherry_runs", {})[args.cherry_run_id] = payload
        parent["status"] = "RUNNING"
        parent["stage"] = "resume_postprocess_fast"
        parent["error"] = ""
        direct_video_skills._save(parent)
        worker.join(timeout=5)

    payload = cherry_skills.run_status(args.cherry_run_id, include_gpu=False)
    parent = direct_video_skills._load(args.parent_run_id) or parent
    parent.setdefault("children", {})["cherry"] = payload
    parent["children"].setdefault("cherry_runs", {})[args.cherry_run_id] = payload
    direct_video_skills._save(parent)
    if str(payload.get("status") or "").upper() != "DONE":
        raise RuntimeError(
            f"accelerated Cherry run ended as {payload.get('status')}: {payload.get('error') or ''}"
        )

    result = direct_video_skills.resume_from_postprocess(args.parent_run_id, resend=True)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

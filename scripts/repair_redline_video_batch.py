from __future__ import annotations

import argparse
import json
import os
import sys

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills import direct_video_skills


def main() -> int:
    parser = argparse.ArgumentParser(description="Fully reprocess corrupted direct-video runs in a strict queue.")
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--no-resend", action="store_true")
    args = parser.parse_args()

    init_db(settings.data_db_path)
    create_tables()

    queued: list[dict[str, str]] = []
    total = len(args.run_ids)
    for position, run_id in enumerate(args.run_ids, start=1):
        run = direct_video_skills._load(run_id)
        if not run:
            print(json.dumps({"ok": False, "run_id": run_id, "error": "run not found"}, ensure_ascii=False), flush=True)
            return 2
        for item in run.get("videos") or []:
            item["source_name"] = direct_video_skills._source_display_name(run, item)
        run["status"] = "QUEUED"
        run["stage"] = "repair_queued"
        run["error"] = ""
        run["repair_batch"] = {"position": position, "total": total}
        run["worker_pid"] = os.getpid()
        run["worker_mode"] = "persistent_repair_batch"
        direct_video_skills._append_log(run, f"红线修复已排队：第 {position}/{total} 个，按原视频名独立处理")
        direct_video_skills._save(run)
        queued.append({"run_id": run_id, "name": run["videos"][0]["source_name"]})
    print(json.dumps({"event": "queued", "items": queued}, ensure_ascii=False), flush=True)

    failures = 0
    for position, run_id in enumerate(args.run_ids, start=1):
        print(json.dumps({"event": "start", "position": position, "total": total, "run_id": run_id}, ensure_ascii=False), flush=True)
        result = direct_video_skills.repair_from_frames(run_id, resend=not args.no_resend)
        print(json.dumps({"event": "finish", "position": position, "total": total, **result}, ensure_ascii=False), flush=True)
        if not result.get("ok"):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

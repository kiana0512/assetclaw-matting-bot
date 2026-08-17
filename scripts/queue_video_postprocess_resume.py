from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from assetclaw_matting.skills import direct_video_skills


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def queue(wait_for_run_id: str, target_run_id: str) -> dict[str, object]:
    target = direct_video_skills._load(target_run_id)
    if not target:
        raise RuntimeError(f"direct video run not found: {target_run_id}")
    target["status"] = "QUEUED"
    target["stage"] = "waiting_cherry_queue"
    target["recovery_from_stage"] = "postprocess"
    target["error"] = ""
    target["worker_pid"] = 0
    target["updated_at"] = _now()
    direct_video_skills._append_log(
        target,
        f"Cherry 浏览器运行时排队：等待 {wait_for_run_id} 完成后自动继续。",
    )
    direct_video_skills._save(target)

    while True:
        predecessor = direct_video_skills._load(wait_for_run_id) or {}
        predecessor_status = str(predecessor.get("status") or "").upper()
        predecessor_pid = int(predecessor.get("worker_pid") or 0)
        predecessor_stage = str(predecessor.get("stage") or "").lower()
        cherry = (predecessor.get("children") or {}).get("cherry") or {}
        cherry_finished = str(cherry.get("status") or "").upper() == "DONE"
        packaging_or_later = predecessor_stage in {
            "zip", "resume_zip", "delivery", "resume_delivery", "done", "done_matte_only"
        }
        # Cherry is the only exclusive resource. Packaging and Feishu upload
        # intentionally overlap with the next task's Cherry processing.
        if cherry_finished or packaging_or_later:
            break
        if predecessor_status not in {"RUNNING", "QUEUED", "PENDING"} and predecessor_pid <= 0:
            break
        time.sleep(5)

    target = direct_video_skills._load(target_run_id) or target
    if str(target.get("status") or "").upper() == "CANCELED":
        return {"ok": False, "run_id": target_run_id, "status": "CANCELED"}
    target["status"] = "QUEUED"
    target["stage"] = "character_resolved"
    target["recovery_from_stage"] = "postprocess"
    target["error"] = ""
    target["worker_pid"] = 0
    target["updated_at"] = _now()
    direct_video_skills._save(target)
    started = direct_video_skills._start_worker(target_run_id, recover=True)
    return {"ok": started, "run_id": target_run_id, "worker_started": started}


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue one Cherry resume behind another video run.")
    parser.add_argument("--wait-for", required=True)
    parser.add_argument("target_run_id")
    args = parser.parse_args()
    print(json.dumps(queue(args.wait_for, args.target_run_id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import sys

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills import direct_image_skills


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: retry_direct_image_on_gpu.py IMG_<id>", file=sys.stderr)
        return 2

    init_db(settings.data_db_path)
    create_tables()
    run_id = sys.argv[1]
    run = direct_image_skills._load(run_id)
    if not run:
        print(json.dumps({"ok": False, "error": "run not found", "run_id": run_id}))
        return 1

    run["status"] = "QUEUED"
    run["stage"] = "gpu_retry_queued"
    run["matting_backend"] = "gpu_control"
    run["worker_pid"] = os.getpid()
    run["worker_mode"] = "operator_gpu_retry"
    run["error"] = ""
    direct_image_skills._append_log(
        run,
        "本机显存不足，按操作员要求切换 GPU 集群重新抠图。",
    )
    direct_image_skills._save(run)

    direct_image_skills._worker_once(run_id)
    result = direct_image_skills._load(run_id) or run
    print(json.dumps(direct_image_skills._public(result), ensure_ascii=False))
    return 0 if str(result.get("status") or "") not in {"FAILED", "DONE_WITH_ERRORS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

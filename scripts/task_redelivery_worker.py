from __future__ import annotations

import json
import sys

from assetclaw_matting.services.task_redelivery_service import run_redelivery


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: task_redelivery_worker.py <image|video> <run_id> <request_id>")
        return 2
    result = run_redelivery(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

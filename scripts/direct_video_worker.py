from __future__ import annotations

import argparse
import json
import sys

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills import direct_video_skills


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent direct-video worker.")
    parser.add_argument("run_id")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    init_db(settings.data_db_path)
    create_tables()
    result = direct_video_skills.run_worker_process(args.run_id, recover=args.recover)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.comfyui_skills import _run_worker, _set_run_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one tracked ComfyUI worker in the foreground.")
    parser.add_argument("run_id")
    args = parser.parse_args()

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()
    _set_run_status(args.run_id, "RUNNING")
    _run_worker(args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

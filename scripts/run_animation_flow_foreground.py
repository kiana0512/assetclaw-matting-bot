from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.runtime_context import set_runtime_context
from assetclaw_matting.skills.animation_flow_skills import run_start, run_status


TERMINAL_STATUSES = {"DONE", "FAILED", "CANCELED", "BLOCKED", "DONE_WITH_ERRORS"}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run animation_flow.start in a persistent foreground process.")
    parser.add_argument("--date-root", required=True)
    parser.add_argument("--workflow-path", default="")
    parser.add_argument("--unity-project", default=str(settings.unity_project_dir))
    parser.add_argument("--p4-stream", default="//streams/rel_0.0.1")
    parser.add_argument("--package", default="both", choices=["both", "scene", "emoji"])
    parser.add_argument("--unity-import-mode", default="import", choices=["import", "iteration"])
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--notify-interval-seconds", type=int, default=60)
    parser.add_argument("--allow-p4-writes", action="store_true")
    parser.add_argument("--fake-matting-from-frames", action="store_true")
    parser.add_argument("--priority-character", action="append", default=[])
    parser.add_argument("--run-file", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    settings.ensure_dirs()
    init_db(settings.data_db_path)
    create_tables()

    if settings.feishu_default_notify_chat_id:
        set_runtime_context(channel="feishu", chat_id=settings.feishu_default_notify_chat_id)

    result = run_start(
        date_root=args.date_root,
        workflow_path=args.workflow_path or None,
        unity_project=args.unity_project,
        p4_stream=args.p4_stream,
        package=args.package,
        unity_import_mode=args.unity_import_mode,
        fps=args.fps,
        notify_interval_seconds=args.notify_interval_seconds,
        allow_p4_writes=args.allow_p4_writes,
        fake_matting_from_frames=args.fake_matting_from_frames,
        priority_characters=args.priority_character or ["casualheather"],
    )
    run_id = str(result.get("run_id") or "")
    _write_json(Path(args.run_file), result)
    print(json.dumps({"run_id": run_id, "date_root": args.date_root}, ensure_ascii=False), flush=True)

    while True:
        status = run_status(run_id)
        _write_json(Path(args.status_file), status)
        print(json.dumps({
            "run_id": run_id,
            "status": status.get("status"),
            "stage": status.get("current_stage"),
            "updated_at": status.get("updated_at"),
            "error": status.get("error") or "",
        }, ensure_ascii=False), flush=True)
        if str(status.get("status") or "").upper() in TERMINAL_STATUSES:
            return 0 if status.get("ok") and str(status.get("status")).upper() == "DONE" else 1
        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())

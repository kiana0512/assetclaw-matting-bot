import time

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.cherry_skills import run_start, run_status


init_db(settings.data_db_path)
create_tables()

run = run_start(
    input_dir=r"E:\animation_automation\2026-06-02\_idle_replacements\20260604_153832\work\matte_danny_to_gary",
    output_dir=r"E:\animation_automation\2026-06-02\_idle_replacements\20260604_153832\work\smooth",
    recursive=True,
    max_images=50000,
    skip_existing=False,
    notify_interval_seconds=300,
    use_denoise=True,
    use_smooth=False,
    use_resize=True,
    resize_width=256,
    resize_height=256,
    use_sharpen=True,
)

run_id = run["run_id"]
print(f"run_id={run_id} total={run['total']} seq={run['sequence_count']}", flush=True)
print(f"options={run['options']}", flush=True)

while True:
    status = run_status(run_id, include_gpu=False)
    print(
        "status={status} completed={completed} failed={failed} total={total} last={last}".format(
            status=status.get("status"),
            completed=status.get("completed"),
            failed=status.get("failed"),
            total=status.get("total"),
            last=status.get("last_completed"),
        ),
        flush=True,
    )
    if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
        break
    time.sleep(10)

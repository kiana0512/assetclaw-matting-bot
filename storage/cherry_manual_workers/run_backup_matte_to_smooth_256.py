import time
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.config import settings
from assetclaw_matting.skills.cherry_skills import run_start, run_status

init_db(settings.data_db_path)
create_tables()
run = run_start(
    input_dir=r'E:\animation_automation\2026-06-02\_cherry_resize_backups\20260604_161404\matte',
    output_dir=r'E:\animation_automation\2026-06-02\smooth',
    recursive=True,
    max_images=50000,
    skip_existing=False,
    notify_interval_seconds=300,
    resize_width=256,
    resize_height=256,
)
run_id = run['run_id']
print(f'run_id={run_id} total={run["total"]} seq={run["sequence_count"]}', flush=True)
while True:
    s = run_status(run_id, include_gpu=False)
    print(f"status={s.get('status')} completed={s.get('completed')} failed={s.get('failed')} total={s.get('total')} last={s.get('last_completed')}", flush=True)
    if s.get('status') in {'DONE','DONE_WITH_ERRORS','FAILED','CANCELED'}:
        break
    time.sleep(10)

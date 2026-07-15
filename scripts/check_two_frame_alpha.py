from __future__ import annotations

import shutil
import time
from pathlib import Path

from PIL import Image

from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.comfyui_skills import run_start, run_status


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "storage/real_pipeline_check/frames_two_for_alpha"
DST = ROOT / "storage/real_pipeline_check/matte_two_frame_alpha"


def _alpha_stats(path: Path) -> str:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        transparent = sum(1 for value in alpha.getdata() if value < 255)
        return (
            f"{path.relative_to(ROOT)} mode={image.mode} size={image.size} "
            f"alpha={alpha.getextrema()} transparent_pixels={transparent}"
        )


def main() -> None:
    init_db(ROOT / "data/assetclaw.db")
    create_tables()
    shutil.rmtree(SRC, ignore_errors=True)
    shutil.rmtree(DST, ignore_errors=True)
    (SRC / "sample").mkdir(parents=True, exist_ok=True)
    for name in ("0001.png", "0002.png"):
        shutil.copy2(
            ROOT / "storage/real_pipeline_check/frames_subset/sample" / name,
            SRC / "sample" / name,
        )

    started = run_start(
        input_dir=str(SRC),
        output_dir=str(DST),
        max_images=2,
        notify_interval_seconds=3600,
    )
    run_id = started["run_id"]
    print(f"run_id={run_id}")
    status = {}
    for _ in range(180):
        status = run_status(run_id, include_gpu=False)
        print(
            "status="
            f"{status.get('status')} "
            f"{status.get('completed')}/{status.get('total')} "
            f"failed={status.get('failed')}"
        )
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            break
        time.sleep(2)
    print(f"final={status}")
    for path in sorted(DST.rglob("*.png")):
        print(_alpha_stats(path))


if __name__ == "__main__":
    main()

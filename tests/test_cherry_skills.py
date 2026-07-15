from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.cherry_skills import info, preset_options, run_list, run_preview, run_start, run_status
from assetclaw_matting.skills.registry import get_skill_meta


def setup_module() -> None:
    init_db(Path("E:/assetclaw-matting-bot/data/test_assetclaw.db"))
    create_tables()


def _make_frame(path: Path, alpha: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 8), (255, 0, 0, alpha)).save(path)


def _wait_done(run_id: str) -> dict:
    status = {}
    for _ in range(300):
        status = run_status(run_id, include_gpu=False)
        if status.get("status") in {"DONE", "DONE_WITH_ERRORS", "FAILED", "CANCELED"}:
            return status
        time.sleep(0.05)
    return status


def test_cherry_info_preview_and_real_processing() -> None:
    src = Path("E:/assetclaw-matting-bot/storage/debug/cherry_input")
    dst = Path("E:/assetclaw-matting-bot/storage/debug/cherry_output")
    _make_frame(src / "seq_a" / "001.png", 80)
    _make_frame(src / "seq_a" / "002.png", 160)

    available = info()
    assert available["exists"] is True
    assert available["source_path"].endswith("cherry-postprocess.html")
    assert available["engine"] == "headless_chrome_html"
    assert "fringe" in available["steps"]
    assert "resize2" in available["steps"]
    assert available["defaults"]["use_denoise"] is True
    assert available["defaults"]["engine"] == "headless_chrome_html"
    assert available["defaults"]["html_feather_enabled"] is True
    assert available["defaults"]["use_smooth"] is False
    assert available["defaults"]["profile"] == "auto"
    assert available["defaults"]["auto_profile_by_size"] is True
    assert available["defaults"]["resize2_width"] == 384
    assert available["defaults"]["resize2_height"] == 512

    half = preset_options("half")
    assert half["engine"] == "headless_chrome_html"
    assert half["html_feather_enabled"] is False
    assert half["use_resize2"] is True
    assert half["use_sharp2"] is False
    assert half["resize_width"] == 256
    assert half["resize_height"] == 256
    assert half["use_smooth"] is False

    auto = preset_options("auto")
    assert auto["profile"] == "auto"
    assert auto["auto_profile_by_size"] is True

    preview = run_preview(str(src), str(dst), use_resize=False, use_sharpen=False)
    assert preview["total"] == 2
    assert preview["sequence_count"] == 1
    assert preview["options"]["use_denoise"] is True

    started = run_start(str(src), str(dst), use_resize=False, use_sharpen=False, notify_interval_seconds=60)
    status = _wait_done(started["run_id"])

    assert status["status"] == "DONE"
    assert status["completed"] == 2
    assert (dst / "seq_a" / "001.png").exists()
    assert (dst / "seq_a" / "002.png").exists()

    text = format_skill_results([{"ok": True, "skill": "cherry.run_status", "result": status}])
    assert "⌨️ Cherry" in text
    assert "half 256x256" in text


def test_cherry_registry_and_router() -> None:
    assert get_skill_meta("cherry.run_start")["requires_confirmation"] is True
    assert LocalCommandBrain()._infer_tool_calls("对 E:\\output 做 Cherry 平滑处理 输出 E:\\smooth_output")[0].skill == "cherry.run_start"
    call = LocalCommandBrain()._infer_tool_calls(
        "补跑 Cherry 平滑处理 E:\\animation_automation\\2026-06-02\\matte E:\\animation_automation\\2026-06-02\\smooth 跳过已有"
    )[0]
    assert call.skill == "cherry.run_start"
    assert call.arguments["skip_existing"] is True
    no_temporal = LocalCommandBrain()._infer_tool_calls(
        "Cherry 平滑处理 E:\\animation_automation\\2026-06-02\\matte E:\\animation_automation\\2026-06-02\\smooth 不做时序平滑"
    )[0]
    assert no_temporal.arguments["use_smooth"] is False
    temporal = LocalCommandBrain()._infer_tool_calls(
        "Cherry 平滑处理 E:\\animation_automation\\2026-06-02\\matte E:\\animation_automation\\2026-06-02\\smooth 开启时序平滑"
    )[0]
    assert temporal.arguments["use_smooth"] is True
    assert LocalCommandBrain()._infer_tool_calls("现在平滑任务到哪里了")[0].skill == "cherry.run_status"

    listed = run_list(include_finished=True)
    assert listed["ok"] is True

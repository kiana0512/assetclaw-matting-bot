from __future__ import annotations

from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.status_skills import comfyui_status


def test_comfyui_status_formatter_has_details(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "comfyui_fake_mode", True)
    payload = comfyui_status()
    text = format_skill_results([{"ok": True, "skill": "comfyui.status", "result": payload}])
    assert "ComfyUI 状态" in text
    assert "fake mode" in text
    assert settings.comfyui_url in text


def test_gpu_status_formatter_has_details() -> None:
    payload = {
        "ok": True,
        "available": True,
        "gpus": [
            {
                "index": "0",
                "name": "RTX 3090",
                "memory_used_mb": 1000,
                "memory_total_mb": 24576,
                "utilization_gpu_percent": 12,
                "temperature_c": 55,
                "power_draw_w": 120,
            }
        ],
    }
    text = format_skill_results([{"ok": True, "skill": "system.gpu_status", "result": payload}])
    assert "GPU 状态" in text
    assert "RTX 3090" in text
    assert "1000/24576 MB" in text

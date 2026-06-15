from __future__ import annotations

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.registry import get_skill_meta


def test_unity_tools_registry_and_router() -> None:
    assert get_skill_meta("unity_tools.atlas_report")["requires_confirmation"] is True
    assert get_skill_meta("unity_tools.rename_run")["requires_confirmation"] is True

    atlas = LocalCommandBrain()._infer_tool_calls("生成图集大小报告")
    assert atlas[0].skill == "unity_tools.atlas_report"

    status = LocalCommandBrain()._infer_tool_calls("查看 AtlasSizeReport 状态")
    assert status[0].skill == "unity_tools.atlas_status"

    rename = LocalCommandBrain()._infer_tool_calls(
        "预览动画贴图批量重命名 Assets/Art/UI/SpritesAnim/Emoji/Mia/Common Assets/Art/UI/Animation/Emoji/Mia"
    )
    assert rename[0].skill == "unity_tools.rename_preview"
    assert rename[0].arguments["texture_folder"] == "Assets/Art/UI/SpritesAnim/Emoji/Mia/Common"
    assert rename[0].arguments["animation_folder"] == "Assets/Art/UI/Animation/Emoji/Mia"


def test_unity_tools_formatter_outputs_atlas_summary() -> None:
    text = format_skill_results(
        [
            {
                "ok": True,
                "skill": "unity_tools.atlas_status",
                "result": {
                    "unity_project": "D:/Spark/Client",
                    "report_path": "D:/Spark/Client/Assets/TATest/AtlasSizeReport.json",
                    "report_exists": True,
                    "report": {
                        "generatedAt": "2026-06-12 10:00:00",
                        "compressionFormat": "ASTC_6x6",
                        "totalEstimatedSizeMB": 39.25,
                        "totalAtlases": 36,
                        "totalSprites": 946,
                        "categorySummary": {
                            "character": {"estimatedSizeMB": 9.81, "atlasCount": 6, "spriteCount": 138},
                            "chat": {"estimatedSizeMB": 11.38, "atlasCount": 13, "spriteCount": 313},
                            "order": {"estimatedSizeMB": 18.06, "atlasCount": 17, "spriteCount": 495},
                        },
                    },
                },
            }
        ]
    )

    assert "Unity 图集大小报告" in text
    assert "总量：39.25 MB" in text
    assert "角色：9.81 MB" in text

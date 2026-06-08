from __future__ import annotations

from pathlib import Path

from tools.p4_assistant.models import P4FileAction, P4FileChange, P4WorkspaceConfig, ReportData, SafetyResult
from tools.p4_assistant.operations import render_report


def test_report_text_contains_cl_shelf_files_and_safety() -> None:
    workspace = P4WorkspaceConfig(
        name="spark_client_ui",
        p4port="spark-p4.lilithgames.com:1666",
        p4user="keizhang",
        p4client="keizhang_L-20260528ZLGJA_8024",
        root=Path("D:/Spark/Client"),
        managed_paths=("Assets/Art/UI/SpritesAnim/Emoji/...",),
        forbidden_paths=("ProjectSettings/...",),
    )
    files = (
        P4FileChange(local_path="Assets/Art/UI/SpritesAnim/Emoji/a.png", action=P4FileAction.ADD),
        P4FileChange(local_path="Assets/Art/UI/SpritesAnim/Emoji/a.png.meta", action=P4FileAction.ADD),
    )
    safety = SafetyResult(True, warnings=("meta reviewed",), checks={"managed paths only": "PASS", "Unity .meta": "WARNING"})
    text = render_report(ReportData(workspace, "123456", "123456", files, safety, "main"))
    assert "【P4 UI 表情资源导入 - Shelve Only】" in text
    assert "123456" in text
    assert "Shelve-only" in text
    assert "add  Assets/Art/UI/SpritesAnim/Emoji/a.png" in text
    assert "Unity .meta 检查: WARNING" in text
    assert "meta reviewed" in text

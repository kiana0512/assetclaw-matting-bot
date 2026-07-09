from __future__ import annotations

import json
import zipfile
from pathlib import Path

from assetclaw_matting.brain.local_command_brain import LocalCommandBrain
from assetclaw_matting.brain.result_formatter import format_skill_results
from assetclaw_matting.skills.utility_skills import (
    archive_list,
    csv_summary,
    file_count,
    file_manifest,
    file_preview,
    file_search_text,
    json_query,
)


def test_utility_file_search_preview_count_manifest() -> None:
    root = Path("E:/assetclaw-matting-bot/storage/debug/utility")
    root.mkdir(parents=True, exist_ok=True)
    note = root / "note.txt"
    note.write_text("alpha\nComfyUI task\nomega", encoding="utf-8")

    found = file_search_text(str(root), "ComfyUI")
    assert found["count"] == 1
    assert found["items"][0]["line"] == 2

    preview = file_preview(str(note))
    assert "ComfyUI task" in preview["preview"]

    counted = file_count(str(root))
    assert counted["files"] >= 1
    assert counted["text"] >= 1

    manifest = file_manifest(str(root), str(root / "manifest.csv"), format="csv")
    assert manifest["ok"] is True
    assert (root / "manifest.csv").exists()


def test_archive_json_csv_helpers_and_formatter() -> None:
    root = Path("E:/assetclaw-matting-bot/storage/debug/utility_structured")
    root.mkdir(parents=True, exist_ok=True)
    data_path = root / "data.json"
    data_path.write_text(json.dumps({"nodes": [{"type": "LoadImage"}]}, ensure_ascii=False), encoding="utf-8")
    csv_path = root / "table.csv"
    csv_path.write_text("name,count\na,1\n", encoding="utf-8")
    zip_path = root / "archive.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(csv_path, arcname="table.csv")

    assert json_query(str(data_path), "/nodes/0/type")["value"] == "LoadImage"
    assert csv_summary(str(csv_path))["columns"] == ["name", "count"]
    assert archive_list(str(zip_path))["items"][0]["name"] == "table.csv"

    text = format_skill_results([{"ok": True, "skill": "archive.list", "result": archive_list(str(zip_path))}])
    assert "table.csv" in text


def test_local_router_utility_skills() -> None:
    brain = LocalCommandBrain()
    assert brain._infer_tool_calls("统计 E:\\input 里有多少图片")[0].skill == "file.count"
    assert brain._infer_tool_calls("看看这个 zip 里面有哪些文件 E:\\assetclaw-matting-bot\\storage\\debug\\a.zip")[0].skill == "archive.list"
    assert brain._infer_tool_calls("查看这个 CSV 有哪些列 E:\\assetclaw-matting-bot\\storage\\debug\\a.csv")[0].skill == "csv.summary"
    assert brain._infer_tool_calls("看看 E:\\input 有哪些文件")[0].skill == "file.list_allowed"

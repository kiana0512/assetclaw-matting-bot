from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.animation_automation.core import (
    build_unity_ready,
    classify_asset_kind,
    classify_process_variant,
    routed_stage_dir,
    task_key,
    unity_types,
    write_source_manifest,
)


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")


def _record(character: str, animation: str, asset: str, variant: str, types: list[str] | None = None) -> dict:
    return {
        "recordId": f"rec_{character}_{animation}_{asset}_{variant}",
        "character": character,
        "animation": animation,
        "displayName": "待机" if animation == "idle" else animation,
        "assetKind": asset,
        "unityCategory": (types or ["剧情"])[0] if types else "",
        "progress": "待处理",
        "processOption": "时序平滑" if variant == "temporal_smooth" else "",
        "processVariant": variant,
        "types": types or ["剧情"],
        "attachments": [{"name": "a.mp4", "localPath": f"{asset}/{variant}/videos/{character}-{animation}/source.mp4"}],
        "taskKey": task_key(character, animation),
        "skipped": False,
        "skipReason": "",
    }


def test_routing_classifies_asset_kind_and_process_variant() -> None:
    assert classify_asset_kind({"类型": "场景动画"}) == "scene"
    assert classify_asset_kind({"分类": ["表情动画"]}) == "emoji"
    assert classify_asset_kind({"category": "订单 剧情"}) == "emoji"
    assert classify_process_variant({"处理选项": "时序平滑"}) == "temporal_smooth"
    assert classify_process_variant({"处理选项": "temporal smooth"}) == "temporal_smooth"
    assert classify_process_variant({}) == "default"
    assert task_key("Jessica", "idle") == "Jessica-idle"


def test_unity_types_default_scene_and_empty_emoji() -> None:
    assert unity_types({"类型": "角色动画"}, "scene") == ["角色动画"]
    assert unity_types({}, "scene") == ["角色动画"]
    assert unity_types({}, "emoji") == []


def test_build_unity_ready_merges_four_routes_to_two_packages(tmp_path: Path) -> None:
    date_root = tmp_path / "2026-06-09"
    records = [
        _record("heather", "idle", "scene", "temporal_smooth", ["角色动画"]),
        _record("Jessica", "idle", "emoji", "default", ["剧情"]),
        _record("creamy", "happy", "emoji", "temporal_smooth", ["订单", "剧情"]),
    ]
    for record in records:
        root = routed_stage_dir(date_root, record["assetKind"], record["processVariant"], "smooth", record["taskKey"])
        _png(root / "0000.png")
        _png(root / "0001.png")
    write_source_manifest(date_root, {"date": "2026-06-09", "feishu": {}, "records": records, "skipped": []})

    report = build_unity_ready(date_root)

    scene_json = json.loads((date_root / "unity_ready/scene/animation_resource_manifest.json").read_text(encoding="utf-8"))
    emoji_json = json.loads((date_root / "unity_ready/emoji/animation_resource_manifest.json").read_text(encoding="utf-8"))
    assert scene_json["items"]["heather"]["idle"]["types"] == ["角色动画"]
    assert emoji_json["items"]["Jessica"]["idle"]["types"] == ["剧情"]
    assert emoji_json["items"]["creamy"]["happy"]["types"] == ["订单", "剧情"]
    assert (date_root / "unity_ready/scene/frames/heather-idle/0000.png").exists()
    assert (date_root / "unity_ready/emoji/frames/Jessica-idle/0000.png").exists()
    assert not (date_root / "unity_ready/scene/default").exists()
    assert len(report["packages"]["scene"]["tasks"]) == 1
    assert len(report["packages"]["emoji"]["tasks"]) == 2
    assert report["packages"]["emoji"]["tasks"][0]["frameCount"] == 2


def test_scene_and_emoji_same_name_do_not_conflict(tmp_path: Path) -> None:
    date_root = tmp_path / "2026-06-09"
    records = [
        _record("heather", "idle", "scene", "default", ["角色动画"]),
        _record("heather", "idle", "emoji", "default", ["剧情"]),
    ]
    for record in records:
        _png(routed_stage_dir(date_root, record["assetKind"], record["processVariant"], "smooth", record["taskKey"]) / "0000.png")
    write_source_manifest(date_root, {"date": "2026-06-09", "feishu": {}, "records": records, "skipped": []})

    build_unity_ready(date_root)

    assert (date_root / "unity_ready/scene/frames/heather-idle/0000.png").exists()
    assert (date_root / "unity_ready/emoji/frames/heather-idle/0000.png").exists()


def test_same_package_same_source_merges_types(tmp_path: Path) -> None:
    date_root = tmp_path / "2026-06-09"
    first = _record("creamy", "happy", "emoji", "default", ["订单"])
    second = _record("creamy", "happy", "emoji", "default", ["剧情"])
    _png(routed_stage_dir(date_root, "emoji", "default", "smooth", "creamy-happy") / "0000.png")
    write_source_manifest(date_root, {"date": "2026-06-09", "feishu": {}, "records": [first, second], "skipped": []})

    build_unity_ready(date_root)

    manifest = json.loads((date_root / "unity_ready/emoji/animation_resource_manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"]["creamy"]["happy"]["types"] == ["订单", "剧情"]


def test_same_package_duplicate_conflicts(tmp_path: Path) -> None:
    date_root = tmp_path / "2026-06-09"
    records = [
        _record("heather", "idle", "emoji", "default", ["剧情"]),
        _record("heather", "idle", "emoji", "temporal_smooth", ["订单"]),
    ]
    for record in records:
        _png(routed_stage_dir(date_root, record["assetKind"], record["processVariant"], "smooth", record["taskKey"]) / "0000.png")
    write_source_manifest(date_root, {"date": "2026-06-09", "feishu": {}, "records": records, "skipped": []})

    with pytest.raises(ValueError, match="重复任务 heather-idle"):
        build_unity_ready(date_root)


def test_existing_output_requires_overwrite(tmp_path: Path) -> None:
    date_root = tmp_path / "2026-06-09"
    record = _record("Jessica", "idle", "emoji", "default", ["剧情"])
    _png(routed_stage_dir(date_root, "emoji", "default", "smooth", "Jessica-idle") / "0000.png")
    write_source_manifest(date_root, {"date": "2026-06-09", "feishu": {}, "records": [record], "skipped": []})
    (date_root / "unity_ready").mkdir()

    with pytest.raises(FileExistsError):
        build_unity_ready(date_root)
    build_unity_ready(date_root, overwrite=True)
    assert (date_root / "unity_ready/emoji/frames/Jessica-idle/0000.png").exists()


def test_source_manifest_records_skip_and_download_path(tmp_path: Path) -> None:
    date_root = tmp_path / "2026-06-09"
    record = _record("Jessica", "idle", "emoji", "default", ["剧情"])
    skipped = {
        "recordId": "rec_done",
        "character": "gary",
        "animation": "idle",
        "reason": "progress is 已完成",
        "skipped": True,
        "skipReason": "progress is 已完成",
    }
    path = write_source_manifest(date_root, {"date": "2026-06-09", "feishu": {"tableId": "tbl"}, "records": [record, skipped], "skipped": [skipped]})
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["records"][0]["attachments"][0]["localPath"] == "emoji/default/videos/Jessica-idle/source.mp4"
    assert payload["records"][0]["processVariant"] == "default"
    assert payload["skipped"][0]["reason"] == "progress is 已完成"


def test_missing_frame_warns_and_missing_smooth_errors(tmp_path: Path) -> None:
    date_root = tmp_path / "2026-06-09"
    record = _record("Jessica", "idle", "emoji", "default", ["剧情"])
    smooth = routed_stage_dir(date_root, "emoji", "default", "smooth", "Jessica-idle")
    _png(smooth / "0000.png")
    _png(smooth / "0002.png")
    write_source_manifest(date_root, {"date": "2026-06-09", "feishu": {}, "records": [record], "skipped": []})
    report = build_unity_ready(date_root)
    assert any("0032" not in warning and "0001.png" in warning for warning in report["warnings"])

    missing = _record("creamy", "happy", "emoji", "default", ["剧情"])
    write_source_manifest(date_root / "missing", {"date": "2026-06-09", "feishu": {}, "records": [missing], "skipped": []})
    with pytest.raises(FileNotFoundError):
        build_unity_ready(date_root / "missing")

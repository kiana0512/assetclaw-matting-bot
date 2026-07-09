from __future__ import annotations

from scripts.export_feishu_table_json import _compact_payload


def test_compact_payload_keeps_only_character_status_business_fields() -> None:
    records = [
        {
            "hierarchy": ["表情动画", "danny", "idle"],
            "fields": {"动画名": "待机", "类型": [{"text": "订单"}, {"text": "剧情"}]},
            "attachments": {"动画": ["danny_idle.mp4"]},
            "video_files": ["danny_idle.mp4"],
        },
        {
            "hierarchy": ["表情动画", "danny"],
            "fields": {},
            "attachments": {},
            "video_files": [],
        },
    ]

    payload, stats = _compact_payload(records)

    assert payload == {
        "danny": {
            "idle": {
                "name": "待机",
                "types": ["订单", "剧情"],
            }
        }
    }
    assert stats["entry_count"] == 1

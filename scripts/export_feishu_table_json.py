from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "feishu_frame_tool"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from feishu_client import FeishuClient, parse_table_url  # noqa: E402


DEFAULT_TABLE_URL = (
    "https://lilithgames.feishu.cn/base/CibAbxkphagGKns1yOJcaGK7nph"
    "?table=tblr2d000xleHj9p&view=vewVr7d7AI"
)
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv")
RAW_SCHEMA_VERSION = "assetclaw.feishu_table.raw.v1"
COMPACT_SCHEMA_VERSION = "assetclaw.animation_table.v1"


def _progress_value(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("text") or raw.get("name") or "")
    if isinstance(raw, list) and raw:
        return _progress_value(raw[0])
    return str(raw)


def _parent_record_id(raw: Any) -> str | None:
    if isinstance(raw, list) and raw:
        first = raw[0]
        ids = first.get("record_ids") if isinstance(first, dict) else None
        if ids:
            return str(ids[0])
    return None


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_parent_field(name: str) -> bool:
    normalized = name.replace("記", "记").replace("錄", "录").lower()
    return normalized in {"父记录", "parent"} or "父记录" in normalized


def _is_attachment(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("file_token") or value.get("name"))
    if isinstance(value, list):
        return any(isinstance(item, dict) and (item.get("file_token") or item.get("name")) for item in value)
    return False


def _attachment_names(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("filename") or "").strip()
        if name:
            names.append(name)
    return names


def _cell_value(value: Any) -> Any:
    if _is_attachment(value):
        return _attachment_names(value)
    if isinstance(value, list):
        return [_cell_value(item) for item in value]
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"url", "tmp_url", "download_url", "file_token"}:
                continue
            kept[key] = _cell_value(item)
        return kept
    return value


def _plain_texts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            text = str(value.get(key) or "").strip()
            if text:
                return [text]
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_plain_texts(item))
        return items
    return [str(value)]


def _first_text(value: Any) -> str:
    texts = _plain_texts(value)
    return texts[0] if texts else ""


def _field_meta(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "field_id": field.get("field_id"),
        "field_name": field.get("field_name"),
        "type": field.get("type"),
        "is_primary": field.get("is_primary"),
        "property": field.get("property") or {},
        "description": field.get("description"),
        "ui_type": field.get("ui_type"),
    }


def _is_attachment_field(field: dict[str, Any]) -> bool:
    return field.get("type") == 17 or field.get("ui_type") == "Attachment"


def _record_label(record: dict[str, Any], role_field: str) -> str:
    fields = record.get("fields") or {}
    return str(_progress_value(fields.get(role_field)) or record.get("record_id") or "")


def _hierarchy(record: dict[str, Any], record_map: dict[str, dict[str, Any]], role_field: str, parent_field: str) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    current_id = str(record.get("record_id") or "")
    while current_id and current_id not in seen and current_id in record_map:
        seen.add(current_id)
        current = record_map[current_id]
        label = _record_label(current, role_field)
        if label:
            parts.append(label)
        fields = current.get("fields") or {}
        current_id = _parent_record_id(fields.get(parent_field)) or ""
    parts.reverse()
    return parts


def _record_payload(
    record: dict[str, Any],
    record_map: dict[str, dict[str, Any]],
    role_field: str,
    parent_field: str,
    attachment_field_names: set[str],
) -> dict[str, Any]:
    raw_fields = record.get("fields") or {}
    fields: dict[str, Any] = {}
    restore_fields: dict[str, Any] = {}
    attachments: dict[str, list[str]] = {}
    parent_record_id = _parent_record_id(raw_fields.get(parent_field))
    for name, value in raw_fields.items():
        if _is_parent_field(name):
            continue
        normalized = _cell_value(value)
        fields[name] = normalized
        if _is_attachment(value):
            attachments[name] = _attachment_names(value)
        elif name not in attachment_field_names:
            restore_fields[name] = value
    hierarchy = _hierarchy(record, record_map, role_field, parent_field)
    return {
        "record_id": record.get("record_id"),
        "created_time": record.get("created_time"),
        "last_modified_time": record.get("last_modified_time"),
        "hierarchy": hierarchy,
        "parent": {
            "field_name": parent_field,
            "record_id": parent_record_id,
            "excluded_from_fields": True,
        },
        "fields": fields,
        "restore_fields": restore_fields,
        "attachments": attachments,
        "video_files": [
            name
            for names in attachments.values()
            for name in names
            if name.lower().endswith(VIDEO_EXTS)
        ],
    }


def _build_role_index(records: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for item in records:
        hierarchy = item.get("hierarchy") or []
        videos = item.get("video_files") or []
        if len(hierarchy) < 3 and not videos:
            continue
        character = hierarchy[-2] if len(hierarchy) >= 2 else ""
        emotion = hierarchy[-1] if hierarchy else ""
        if not character or not emotion:
            continue
        bucket = index.setdefault(character, {})
        bucket[emotion] = {
            "record_id": item.get("record_id"),
            "animation_name": item.get("fields", {}).get("动画名"),
            "type": item.get("fields", {}).get("类型"),
            "progress": item.get("fields", {}).get("进度"),
            "videos": videos,
        }
    return index


def _compact_payload(exported_records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    characters: dict[str, dict[str, Any]] = {}
    total_entries = 0
    entries_with_attachment = 0
    for item in exported_records:
        hierarchy = item.get("hierarchy") or []
        if len(hierarchy) < 3:
            continue
        character = str(hierarchy[-2]).strip()
        status = str(hierarchy[-1]).strip()
        if not character or not status:
            continue
        fields = item.get("fields") or {}
        entry = {
            "name": _first_text(fields.get("动画名")),
            "types": _plain_texts(fields.get("类型")),
        }
        characters.setdefault(character, {})[status] = entry
        total_entries += 1
        if item.get("video_files"):
            entries_with_attachment += 1
    stats = {
        "character_count": len(characters),
        "entry_count": total_entries,
        "entries_with_attachment": entries_with_attachment,
    }
    return characters, stats


def _raw_payload(
    fields: list[dict[str, Any]],
    exported_records: list[dict[str, Any]],
    client: FeishuClient,
    table_url: str,
    parsed: dict[str, str | None],
) -> dict[str, Any]:
    return {
        "schema_version": RAW_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "url": table_url,
            "app_token": client.app_token,
            "table_id": client.table_id,
            "view_id": client.view_id,
            "parsed_url": parsed,
        },
        "rules": {
            "excluded_fields": ["父记录", "父記錄"],
            "attachment_export": "video attachment cells keep file names only",
            "restore_policy": {
                "record_match": "record_id",
                "non_attachment_fields": "restore from records[].restore_fields",
                "attachment_fields": "names are preserved for humans; upload file tokens are required before applying attachments",
                "parent_field": "stored in records[].parent and excluded from records[].fields",
            },
        },
        "field_index": {
            str(field.get("field_name")): _field_meta(field)
            for field in fields
            if not _is_parent_field(str(field.get("field_name") or ""))
        },
        "fields": [
            _field_meta(field)
            for field in fields
            if not _is_parent_field(str(field.get("field_name") or ""))
        ],
        "records": exported_records,
        "characters": _build_role_index(exported_records),
        "stats": {
            "field_count": len([f for f in fields if not _is_parent_field(str(f.get("field_name") or ""))]),
            "record_count": len(exported_records),
            "records_with_video": sum(1 for item in exported_records if item.get("video_files")),
        },
    }


def export_table(config_path: Path, output_path: Path, table_url: str, output_format: str = "compact") -> dict[str, Any]:
    cfg = _load_config(config_path)
    cfg.setdefault("feishu", {})["table_url"] = table_url
    parsed = parse_table_url(table_url)
    client = FeishuClient.from_feishu_config(cfg["feishu"], logger=lambda message: print(message))
    role_field = cfg.get("fields", {}).get("role", "角色")
    parent_field = cfg.get("fields", {}).get("parent", "父记录")

    print("读取字段...")
    fields = client.list_fields()
    print(f"字段：{len(fields)}")
    print("读取记录...")
    records = client.list_records()
    print(f"记录：{len(records)}")

    record_map = {str(record.get("record_id") or ""): record for record in records}
    attachment_field_names = {
        str(field.get("field_name") or "")
        for field in fields
        if _is_attachment_field(field)
    }
    exported_records = [
        _record_payload(
            record,
            record_map,
            role_field=role_field,
            parent_field=parent_field,
            attachment_field_names=attachment_field_names,
        )
        for record in records
    ]
    if output_format == "raw":
        payload = _raw_payload(fields, exported_records, client, table_url, parsed)
        file_payload = payload
    else:
        file_payload, stats = _compact_payload(exported_records)
        payload = {
            "schema_version": COMPACT_SCHEMA_VERSION,
            "source_url": table_url,
            "stats": stats,
            "items": file_payload,
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(file_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Feishu bitable records to JSON.")
    parser.add_argument("--config", default=str(ROOT / "feishu_frame_tool/config.json"))
    parser.add_argument("--output", default=str(ROOT / "storage/feishu_table_exports/animation_table.json"))
    parser.add_argument("--table-url", default=DEFAULT_TABLE_URL)
    parser.add_argument("--format", choices=["compact", "raw"], default="compact")
    args = parser.parse_args()
    payload = export_table(Path(args.config), Path(args.output), args.table_url, output_format=args.format)
    print(f"已导出：{args.output}")
    print(
        "统计："
        f"{payload['stats']}"
    )


if __name__ == "__main__":
    main()

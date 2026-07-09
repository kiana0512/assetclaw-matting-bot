from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "feishu_frame_tool"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from feishu_client import FeishuClient  # noqa: E402


ATTACHMENT_FIELD_TYPE = 17


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_payload(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "assetclaw.feishu_table.raw.v1":
        raise ValueError("只有 raw 导出可以生成写回计划；精简业务 JSON 不直接写回飞书。")
    return data


def _attachment_fields(payload: dict[str, Any]) -> set[str]:
    fields = payload.get("field_index") or {}
    names = set()
    for name, meta in fields.items():
        if meta.get("type") == ATTACHMENT_FIELD_TYPE or meta.get("ui_type") == "Attachment":
            names.add(str(name))
    return names


def _restore_fields(record: dict[str, Any], attachment_fields: set[str]) -> dict[str, Any]:
    values = dict(record.get("restore_fields") or {})
    for name in attachment_fields:
        values.pop(name, None)
    return values


def build_restore_plan(payload: dict[str, Any]) -> dict[str, Any]:
    attachment_fields = _attachment_fields(payload)
    updates = []
    skipped = []
    for record in payload.get("records") or []:
        record_id = str(record.get("record_id") or "")
        if not record_id:
            skipped.append({"reason": "missing_record_id", "record": record})
            continue
        fields = _restore_fields(record, attachment_fields)
        updates.append({
            "record_id": record_id,
            "field_count": len(fields),
            "fields": fields,
            "skipped_attachment_fields": sorted(name for name in attachment_fields if name in (record.get("fields") or {})),
            "parent": record.get("parent") or {},
        })
    return {
        "ok": True,
        "schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "policy": payload.get("rules", {}).get("restore_policy") or {},
        "update_count": len(updates),
        "skipped_count": len(skipped),
        "updates": updates,
        "skipped": skipped,
    }


def apply_restore_plan(config_path: Path, plan: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    cfg = _load_config(config_path)
    source = plan.get("source") or {}
    if source.get("url"):
        cfg.setdefault("feishu", {})["table_url"] = source["url"]
    client = FeishuClient.from_feishu_config(cfg["feishu"], logger=lambda message: print(message))
    applied = 0
    errors = []
    for item in plan.get("updates") or []:
        if limit is not None and applied >= limit:
            break
        try:
            client.update_record_fields(item["record_id"], item.get("fields") or {})
            applied += 1
        except Exception as exc:
            errors.append({"record_id": item.get("record_id"), "error": str(exc)})
    return {"ok": not errors, "applied": applied, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore/update Feishu bitable records from exported JSON.")
    parser.add_argument("--json", default=str(ROOT / "storage/feishu_table_exports/animation_table.json"))
    parser.add_argument("--config", default=str(ROOT / "feishu_frame_tool/config.json"))
    parser.add_argument("--plan-output", default=str(ROOT / "storage/feishu_table_exports/animation_table_restore_plan.json"))
    parser.add_argument("--apply", action="store_true", help="真正写回飞书；默认只生成计划。")
    parser.add_argument("--limit", type=int, default=None, help="最多应用多少条，用于小范围验证。")
    args = parser.parse_args()

    payload = _load_payload(Path(args.json))
    plan = build_restore_plan(payload)
    plan_path = Path(args.plan_output)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"还原计划：{plan_path}")
    print(f"计划更新：{plan['update_count']} 条，跳过：{plan['skipped_count']} 条")
    attachment_skips = sum(len(item.get("skipped_attachment_fields") or []) for item in plan["updates"])
    print(f"附件字段跳过次数：{attachment_skips}")
    if args.apply:
        result = apply_restore_plan(Path(args.config), plan, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("dry-run：未写回飞书。需要真正写回时加 --apply。")


if __name__ == "__main__":
    main()

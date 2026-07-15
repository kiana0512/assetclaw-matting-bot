from __future__ import annotations

from pathlib import Path
from typing import Any

from assetclaw_matting.skills.security import validate_path


DEFAULT_TABLE_URL = (
    "https://lilithgames.feishu.cn/base/CibAbxkphagGKns1yOJcaGK7nph"
    "?table=tblr2d000xleHj9p&view=vewVr7d7AI"
)


def export_json(
    table_url: str | None = None,
    output_path: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings
    from scripts.export_feishu_table_json import export_table

    out = validate_path(
        output_path or settings.storage_dir / "feishu_table_exports" / "animation_table.json",
        must_exist=False,
    )
    cfg = validate_path(
        config_path or settings.assetclaw_root / "feishu_frame_tool" / "config.json",
        must_exist=True,
    )
    payload = export_table(Path(cfg), Path(out), table_url or DEFAULT_TABLE_URL)
    return {
        "ok": True,
        "output_path": str(out),
        "source": payload.get("source") or payload.get("source_url"),
        "schema_version": payload.get("schema_version"),
        "stats": payload.get("stats"),
    }


def restore_plan(
    json_path: str | None = None,
    plan_output_path: str | None = None,
) -> dict[str, Any]:
    import json
    from assetclaw_matting.config import settings
    from scripts.restore_feishu_table_json import build_restore_plan

    src = validate_path(
        json_path or settings.storage_dir / "feishu_table_exports" / "animation_table.json",
        must_exist=True,
    )
    dst = validate_path(
        plan_output_path or settings.storage_dir / "feishu_table_exports" / "animation_table_restore_plan.json",
        must_exist=False,
    )
    payload = json.loads(Path(src).read_text(encoding="utf-8"))
    plan = build_restore_plan(payload)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "json_path": str(src),
        "plan_output_path": str(dst),
        "update_count": plan.get("update_count"),
        "skipped_count": plan.get("skipped_count"),
        "attachment_skip_count": sum(len(item.get("skipped_attachment_fields") or []) for item in plan.get("updates") or []),
    }

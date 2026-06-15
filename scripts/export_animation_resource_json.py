from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def export_from_manifest(
    manifest_path: Path,
    output_path: Path,
    types: list[str] | None = None,
) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    items: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in payload.get("items") or []:
        role = str(entry.get("role") or "").strip()
        emotion = str(entry.get("emotion") or "").strip()
        if not role or not emotion:
            continue
        items.setdefault(role, {})[emotion] = {
            "name": str(entry.get("animation_name") or ""),
            "types": list(types or []),
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"items": items}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Unity animation resource JSON from frame manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--type", action="append", default=[], dest="types")
    args = parser.parse_args()
    export_from_manifest(Path(args.manifest), Path(args.output), types=args.types)
    print(f"已导出：{args.output}")


if __name__ == "__main__":
    main()

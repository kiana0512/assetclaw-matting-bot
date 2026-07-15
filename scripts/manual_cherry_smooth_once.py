from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from assetclaw_matting.config import settings
from assetclaw_matting.services.cherry_html_runner import run_cherry_html, validate_cherry_html_runtime


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _collect_groups(root: Path) -> list[list[Path]]:
    groups: dict[Path, list[Path]] = defaultdict(list)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            groups[path.parent].append(path)
    return [sorted(files, key=lambda item: item.name.lower()) for _, files in sorted(groups.items(), key=lambda item: str(item[0]).lower())]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the authoritative Cherry HTML processor once.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    src = Path(args.input_dir).resolve()
    dst = Path(args.output_dir).resolve()
    if not src.is_dir():
        raise NotADirectoryError(src)
    groups = _collect_groups(src)
    if not groups:
        raise RuntimeError(f"input dir has no images: {src}")

    browser = Path(settings.cherry_browser_path) if settings.cherry_browser_path else None
    runtime = validate_cherry_html_runtime(Path(settings.cherry_postprocess_html_path), browser)
    results = []
    for files in groups:
        result = run_cherry_html(
            Path(settings.cherry_postprocess_html_path),
            src,
            dst,
            files,
            chrome_path=browser,
            timeout_seconds=int(settings.cherry_html_timeout_seconds),
            storage_dir=Path(settings.storage_dir),
        )
        results.append({"count": result.total, "profile": result.profile, "resize": result.resize, "steps": result.steps})
    print(json.dumps({"ok": True, "runtime": runtime, "output_dir": str(dst), "groups": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
